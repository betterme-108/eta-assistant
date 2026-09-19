"""批改引擎：听写批改 + 作业/考试批改（规则判卷 + AI 判定主观题）。

升级后的第一入口——"批改即收集"：
  听写与客观题用确定性规则判卷（不调 AI，快、稳、零成本）；
  短主观题（完成句子/概要补全/任务型阅读）由 AI 判定对错并给理由；
  书面表达（作文）由 AI 分维评分（内容要点/语言准确性/结构连贯/书写规范），
  失分即按错题自动收集；AI 判定失败时置"待判定"由教师把关（不静默给分）；
  所有判错的题自动进入错因分析链路（errors 表待确认），
  与「错题录入」共用同一条归因引擎（classifier.classify，同题同答只调一次 AI）。

题型边界：本系统无语音链路（不做 ASR/TTS），听力等音频类题目不在范围内。

反标签化：发往大模型的内容只有题目与作答文本，学生代号不进入任何模型请求。
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from ..data import db
from ..providers import llm
from . import classifier

# 主观题题型：判定需要 AI 读语义，不适用精确比对
SUBJECTIVE_QTYPES = {"完成句子", "概要补全", "书面表达", "任务型阅读"}

# 书面表达（作文）：分维评分，不适用对错二判
ESSAY_QTYPE = "书面表达"

# 拼写容错：与正确词差距 ≤ 该值视为"拼写有误"（仍判错，但提示是拼写问题）
_MISSPELL_LIMIT = 1
_MISSPELL_LIMIT_LONG = 2   # 长词（≥7 字母）放宽到 2 个字母
_MISSPELL_LONG_LEN = 7

_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


# ---------------------------------------------------------------- 判定基础

def normalize_answer(s: Any) -> str:
    """作答归一化：全半角统一、小写、去空白与标点——客观题比对用。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.lower().strip()
    return _PUNCT_RE.sub("", s)


def edit_distance(a: str, b: str) -> int:
    """莱文斯坦编辑距离（动态规划，两词比对规模小，无需优化库）。"""
    a, b = a or "", b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def word_verdict(student_word: Any, correct_word: str) -> Tuple[str, str]:
    """听写单词语判定。

    返回 (verdict, note)：
      correct   写对
      misspell  拼写有误（与正确词只差少量字母，仍判错）
      blank     空白未写
      wrong     写成了别的词
    """
    n_ans = normalize_answer(student_word)
    n_cor = normalize_answer(correct_word)
    if not n_ans:
        return "blank", "空白未写"
    if n_ans == n_cor:
        return "correct", ""
    dist = edit_distance(n_ans, n_cor)
    limit = _MISSPELL_LIMIT_LONG if len(n_cor) >= _MISSPELL_LONG_LEN else _MISSPELL_LIMIT
    if dist <= limit:
        return "misspell", "拼写有误（与正确词相差 %d 个字母）" % dist
    return "wrong", "写成了别的词"


# ---------------------------------------------------------------- AI 判定主观题

_JUDGE_PROMPT = """【批改判定】你是初中（7-9 年级）英语作业批改助手，按初中英语课标要求判断学生作答是否正确。
【题目】{question}
【题型】{qtype}
【标准答案】{correct}
【学生作答】{answer}
【判定标准】意思与标准答案一致即判对；个别拼写小错不判错；明显语法错误、
漏掉关键信息或意思不符判错。
【输出约束】只输出 JSON：{{"correct": true 或 false, "reason": "一句简短的中文理由"}}"""

_ESSAY_PROMPT = """【作文评分】你是初中英语书面表达评分助手，按初中英语书面表达评分标准（7-9 年级适用）评阅一篇作文。
【题目要求】{question}
【学生作文】{answer}
【满分】{full}
【评分维度】
1. 内容要点：是否覆盖题目全部要点、信息是否完整
2. 语言准确性：语法、拼写、用词是否正确（以初中 7-9 年级所学词汇与语法为限）
3. 结构连贯：逻辑是否清楚、衔接是否自然
4. 书写规范：人称、时态、字数、格式是否符合题目要求
【输出约束】只输出 JSON：
{{"score": 数字（0 到满分，可保留 0.5）, "dimensions": [
  {{"name": "内容要点", "comment": "一句话评价"}},
  {{"name": "语言准确性", "comment": "一句话评价"}},
  {{"name": "结构连贯", "comment": "一句话评价"}},
  {{"name": "书写规范", "comment": "一句话评价"}}
], "reason": "总评一句话（中文）"}}
【禁止】不得对学生个人能力下结论——只评这篇作文本身。"""


def _judge_subjective(question: str, qtype: str, correct: str,
                      answer: str) -> Dict[str, Any]:
    """短主观题 AI 判定：{correct: bool|None, reason}；AI 失败 → correct=None 待判定。"""
    messages = [
        {"role": "system",
         "content": "你是作业批改判定引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user",
         "content": _JUDGE_PROMPT.format(
             question=question or "（无题干）", qtype=qtype or "主观题",
             correct=correct or "（未提供）", answer=answer or "（空白未答）")},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.1)
    except llm.LLMError:
        return {"correct": None, "reason": "AI 判定失败，请您人工判断"}
    if isinstance(data, dict) and isinstance(data.get("correct"), bool):
        return {"correct": data["correct"],
                "reason": str(data.get("reason", ""))[:200]}
    return {"correct": None, "reason": "AI 未能给出结论，请您人工判断"}


def _judge_essay(question: str, answer: str, full: float) -> Optional[Dict[str, Any]]:
    """书面表达分维评分：{score, dimensions, reason}；AI 失败返回 None（待判定）。"""
    messages = [
        {"role": "system",
         "content": "你是书面表达评分引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user",
         "content": _ESSAY_PROMPT.format(
             question=question or "（无题目要求）", answer=answer or "（空白未答）",
             full=full)},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.2)
    except llm.LLMError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        return None
    if not (0 <= score <= full):
        return None
    dims = []
    for d in (data.get("dimensions") or [])[:4]:
        if isinstance(d, dict) and d.get("name"):
            dims.append({"name": str(d["name"])[:20],
                         "comment": str(d.get("comment", ""))[:120]})
    return {"score": score, "dimensions": dims,
            "reason": str(data.get("reason", ""))[:200]}


# ---------------------------------------------------------------- AI 总结与建议（导出用）

_FEEDBACK_PROMPT = """【学生反馈】你是初中（7-9 年级）英语老师，请针对下面这份批改结果写一段简短总结和学习建议。
【批改明细】
{lines}
【要求】
1. summary：一句话概括这份作答反映出的主要问题（只描述错误本身，不评价人）
2. advice：给出两到三条具体、可执行的学习建议（针对出现的错误类型）
【输出约束】只输出 JSON：{{"summary": "一句话中文总结", "advice": "两到三条建议，用分号隔开"}}
【禁止】不得对学生个人能力下结论（如“基础薄弱”“成绩差”）——只描述错误，不评价人"""


def student_feedback(items: List[Dict[str, Any]], kind: str = "assignment") -> Dict[str, str]:
    """批改结果 → AI 总结与建议（导出报告用）。

    只有错题才送进大模型（反标签化：学号不进入任何模型请求）；
    AI 失败返回空串——导出照常可用，总结栏留空即可。
    """
    lines = []
    for it in items:
        if it.get("verdict") == "correct":
            continue
        if kind == "dictation":
            lines.append("第%d词：应写 %s（%s），作答「%s」（%s）" % (
                it.get("no"), it.get("en"), it.get("zh"),
                it.get("answer") or "空白", it.get("note") or ""))
        else:
            lines.append("第%d题（%s）：标准答案 %s；作答「%s」（%s）" % (
                it.get("no"), it.get("qtype"), it.get("correct") or "",
                it.get("answer") or "空白", it.get("note") or ""))
    if not lines:
        return {"summary": "本次作答全部正确。",
                "advice": "可适当拓展同类练习，巩固掌握。"}
    messages = [
        {"role": "system",
         "content": "你是批改结果总结引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user", "content": _FEEDBACK_PROMPT.format(lines="\n".join(lines[:20]))},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.3)
    except llm.LLMError:
        return {"summary": "", "advice": ""}
    if not isinstance(data, dict):
        return {"summary": "", "advice": ""}
    return {"summary": str(data.get("summary", ""))[:200],
            "advice": str(data.get("advice", ""))[:300]}


# ---------------------------------------------------------------- 听写批改

def _classify_wrong_word(zh: str, en: str, answer: str,
                         cache: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
    """错词 → 错因类别（同词同错只调一次 AI）。"""
    key = (en, answer or "")
    if key not in cache:
        question = "听写：中文「%s」，应写英文「%s」" % (zh or "", en)
        cache[key] = classifier.classify(question, answer or "", en, "听写")
    return cache[key]


def dictation_check(words: List[Dict[str, Any]],
                    answers: List[Dict[str, Any]],
                    unit: Optional[str], week: Optional[int],
                    class_name: str, title: str = "",
                    exam_type: str = "",
                    batch_no: str = "") -> Dict[str, Any]:
    """听写批改：逐词规则判卷 → 错词归因入库（待确认）→ 成绩单。

    words:   [{no, en, zh}] 词表（no 从 1 开始）
    answers: [{student_code, items: [作答…]}] items 按词表顺序对齐，不足补空
    """
    total_words = len(words)
    results: List[Dict[str, Any]] = []
    ai_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
    roster = {s["student_code"] for s in db.list_students(class_name or "")}
    wrong_items = inserted = 0
    inserted_ids: List[int] = []

    for ans in answers:
        code = (ans.get("student_code") or "").strip()
        items = list(ans.get("items") or [])
        while len(items) < total_words:
            items.append("")
        per_word: List[Dict[str, Any]] = []
        for idx, w in enumerate(words):
            answer = items[idx]
            verdict, note = word_verdict(answer, w.get("en", ""))
            per_word.append({
                "no": w.get("no", idx + 1),
                "en": w.get("en", ""),
                "zh": w.get("zh", ""),
                "answer": answer,
                "verdict": verdict,
                "note": note,
            })
            if verdict == "correct":
                continue
            wrong_items += 1
            ai = _classify_wrong_word(w.get("zh", ""), w.get("en", ""),
                                      answer, ai_cache)
            err_id = db.insert_error({
                "student_code": code,
                "class_name": class_name or "",
                "question": "听写：中文「%s」，应写英文「%s」" % (w.get("zh", "") or "", w.get("en", "")),
                "answer": answer,
                "correct": w.get("en", ""),
                "qtype": "听写",
                "options": None, "unit": unit, "week": week,
                "exam_type": exam_type, "batch_no": batch_no,
                "category_id": ai["category_id"], "evidence": ai["evidence"],
                "teaching_point": ai["teaching_point"], "confidence": ai["confidence"],
                "needs_review": ai["needs_review"], "behavior": None,
            })
            inserted += 1
            inserted_ids.append(err_id)
        correct_n = sum(1 for p in per_word if p["verdict"] == "correct")
        results.append({
            "student_code": code,
            "correct_n": correct_n,
            "total": total_words,
            "score": round(correct_n / total_words * 100, 1) if total_words else 0,
            "in_roster": code in roster if code else False,
            "items": per_word,
        })

    full_mark = sum(1 for r in results if r["correct_n"] == total_words)
    report = {
        "kind": "dictation",
        "title": title or "听写批改",
        "meta": {"class_name": class_name or "", "unit": unit, "week": week,
                 "exam_type": exam_type, "batch_no": batch_no,
                 "words": total_words, "students": len(results)},
        "stats": {
            "students": len(results),
            "total_words": total_words,
            "avg_score": round(sum(r["score"] for r in results) / len(results), 1)
                         if results else 0,
            "full_mark": full_mark,
            "wrong_items": wrong_items,
            "inserted_errors": inserted,
            "uncertain": 0,
        },
        "results": results,
        "inserted_error_ids": inserted_ids,
    }
    return report


# ---------------------------------------------------------------- 作业/考试批改

def _objective_verdict(answer: Any, correct: Any) -> Tuple[str, str]:
    """客观题判定：归一化后精确比对 → correct / wrong。"""
    if not normalize_answer(answer):
        return "wrong", "空白未答"
    if normalize_answer(answer) == normalize_answer(correct):
        return "correct", ""
    return "wrong", "与标准答案不符"


def assignment_check(key: List[Dict[str, Any]],
                     answers: List[Dict[str, Any]],
                     unit: Optional[str], week: Optional[int],
                     class_name: str, title: str = "",
                     exam_type: str = "",
                     folder: str = "",
                     batch_no: str = "") -> Dict[str, Any]:
    """作业/考试批改：客观题规则判卷 + 主观题 AI 判定 → 错题归因入库 → 成绩单。

    key:     [{no, question, qtype, answer, score, subjective}]（no 从 1 开始）
    answers: [{student_code, name, files, items: [{no, answer, file, verdict?}
               （verdict 为批改API预判：correct/wrong/blank，直入判定）
               或按题号顺序的字符串列表}]
    folder / batch_no: 批次文件夹全名与唯一标识（三段式命名），随报告 meta 留存。
    item 的 file（来源照片，学生/文件名）写入错题 source（唯一标识/学生/文件名），
    实现错题到上传文件的关联；报告携带 inserted_error_ids 供路由层在批改记录
    建立后回填 errors.session_id（入库前 pop，不随报告留存）。
    """
    items_by_no: Dict[int, Dict[str, Any]] = {}
    for k in key:
        no = k.get("no")
        items_by_no[no] = k

    results: List[Dict[str, Any]] = []
    ai_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
    judge_cache: Dict[Tuple[int, str], Dict[str, Any]] = {}
    roster = {s["student_code"] for s in db.list_students(class_name or "")}
    wrong_items = uncertain = inserted = 0
    inserted_ids: List[int] = []

    def _is_subjective(k: Dict[str, Any]) -> bool:
        if k.get("subjective") is not None:
            return bool(k["subjective"])
        return (k.get("qtype") or "") in SUBJECTIVE_QTYPES

    def _judge_item(k: Dict[str, Any], answer: str, no: int,
                    hw_verdict: str = "") -> Dict[str, Any]:
        """单题判定（带缓存）：返回 {verdict, note, got, rubric}。

        批改API预判（verdict）直入优先——它看的是手写图原貌，比文字
        规则比对可靠（作文除外，分维评分不受对错二判影响）；
        客观题精确比对；短主观题 AI 对错判定；书面表达 AI 分维评分（got=实际得分）。
        """
        qtype = k.get("qtype") or ""
        full = float(k.get("score") or 0)
        if hw_verdict in ("correct", "wrong", "blank") and qtype != ESSAY_QTYPE:
            if hw_verdict == "correct":
                return {"verdict": "correct", "note": "智能批改判定正确",
                        "got": full, "rubric": None}
            return {"verdict": "wrong",
                    "note": "空白未答" if hw_verdict == "blank" else "智能批改判定有误",
                    "got": 0, "rubric": None}
        if _is_subjective(k):
            jkey = ("s", no, answer or "")
            if qtype == ESSAY_QTYPE:
                if jkey not in judge_cache:
                    judge_cache[jkey] = _judge_essay(k.get("question", ""), answer or "", full)
                j = judge_cache[jkey]
                if j is None:
                    return {"verdict": "uncertain", "note": "AI 评分失败，请您人工评分",
                            "got": None, "rubric": None}
                got = round(float(j["score"]), 1)
                if got >= full:
                    return {"verdict": "correct", "note": j["reason"],
                            "got": full, "rubric": j["dimensions"]}
                return {"verdict": "wrong", "note": j["reason"],
                        "got": got, "rubric": j["dimensions"]}
            if jkey not in judge_cache:
                judge_cache[jkey] = _judge_subjective(
                    k.get("question", ""), qtype, k.get("answer", ""), answer or "")
            j = judge_cache[jkey]
            if j["correct"] is True:
                return {"verdict": "correct", "note": j["reason"], "got": full, "rubric": None}
            if j["correct"] is False:
                return {"verdict": "wrong", "note": j["reason"], "got": 0, "rubric": None}
            return {"verdict": "uncertain", "note": j["reason"], "got": None, "rubric": None}
        verdict, note = _objective_verdict(answer, k.get("answer", ""))
        return {"verdict": verdict, "note": note,
                "got": full if verdict == "correct" else 0, "rubric": None}

    for ans in answers:
        code = (ans.get("student_code") or "").strip()
        raw_items = list(ans.get("items") or [])
        # 兼容两种作答形态：按题号对象列表（可携带来源 file）/ 按题号顺序的字符串列表
        if raw_items and isinstance(raw_items[0], dict):
            pairs = [(int(it.get("no")), it.get("answer", ""), it.get("file") or "",
                      str(it.get("verdict") or ""))
                     for it in raw_items]
        else:
            ordered = sorted(items_by_no)
            pairs = [(no, raw_items[i], "", "") for i, no in enumerate(ordered)
                     if i < len(raw_items)]
            for no in ordered[len(pairs):]:
                pairs.append((no, "", "", ""))
        per_item: List[Dict[str, Any]] = []
        judged_score = 0.0
        judged_total = 0.0
        for no, answer, file_src, hw_verdict in pairs:
            k = items_by_no.get(no)
            if not k:
                continue
            jr = _judge_item(k, answer, no, hw_verdict)
            item = {
                "no": no,
                "question": k.get("question", ""),
                "qtype": k.get("qtype", ""),
                "answer": answer or "",
                "correct": k.get("answer", ""),
                "score": float(k.get("score") or 0),
                "verdict": jr["verdict"],
                "note": jr["note"],
                "got": jr["got"],
                "rubric": jr["rubric"],
                "subjective": _is_subjective(k),
                # 选择题选项与阅读/完形原文随成绩单透传（拍照批改解析带出），
                # 错题入库时同源写入，保证「错题确认」页能完整展示
                "options": list(k.get("options") or []),
                "passage": k.get("passage") or "",
            }
            per_item.append(item)
            if jr["verdict"] == "correct":
                judged_score += float(k.get("score") or 0)
                judged_total += float(k.get("score") or 0)
            elif jr["verdict"] == "wrong":
                judged_total += float(k.get("score") or 0)
                judged_score += float(jr["got"] or 0)
                wrong_items += 1
                akey = (item["question"], answer or "")
                if akey not in ai_cache:
                    # 反标签化：归因 payload 只有题目本身，学号不进入任何模型请求；
                    # 选项与阅读/完形原文一并传入，归因才能定位干扰项/结合原文判断
                    ai_cache[akey] = classifier.classify(
                        item["question"], answer or "", k.get("answer", ""),
                        k.get("qtype", ""), options=k.get("options"),
                        passage=k.get("passage") or "")
                ai = ai_cache[akey]
                err_id = db.insert_error({
                    "student_code": code,
                    "class_name": class_name or "",
                    "question": item["question"], "answer": answer,
                    "correct": k.get("answer", ""), "qtype": k.get("qtype", ""),
                    "options": k.get("options") or None,
                    "passage": k.get("passage") or "",
                    "unit": unit, "week": week,
                    "exam_type": exam_type, "batch_no": batch_no,
                    "category_id": ai["category_id"], "evidence": ai["evidence"],
                    "teaching_point": ai["teaching_point"],
                    "confidence": ai["confidence"],
                    "needs_review": ai["needs_review"], "behavior": None,
                    "source": "/".join(x for x in (batch_no, file_src) if x),
                })
                inserted += 1
                inserted_ids.append(err_id)
            else:
                uncertain += 1

        results.append({
            "student_code": code,
            "name": (ans.get("name") or "").strip(),
            "files": [f for f in (ans.get("files") or []) if f],
            "correct_n": sum(1 for p in per_item if p["verdict"] == "correct"),
            "judged_n": sum(1 for p in per_item if p["verdict"] != "uncertain"),
            "uncertain_n": sum(1 for p in per_item if p["verdict"] == "uncertain"),
            "score": round(judged_score / judged_total * 100, 1) if judged_total else None,
            "in_roster": code in roster if code else False,
            "items": per_item,
        })

    scored = [r for r in results if r["score"] is not None]
    report = {
        "kind": "assignment",
        "title": title or batch_no or "作业批改",
        "meta": {"class_name": class_name or "", "unit": unit, "week": week,
                 "exam_type": exam_type, "batch_no": batch_no, "folder": folder,
                 "items": len(key), "students": len(results)},
        "stats": {
            "students": len(results),
            "items": len(key),
            "avg_score": round(sum(r["score"] for r in scored) / len(scored), 1)
                         if scored else None,
            "wrong_items": wrong_items,
            "uncertain": uncertain,
            "inserted_errors": inserted,
        },
        "results": results,
        "inserted_error_ids": inserted_ids,
    }
    return report
