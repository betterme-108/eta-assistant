"""归因引擎：两次调用分离。

第一次（逐题归因）：注入本体库 36 类的 signals+examples + 题干/作答 → 单错因 JSON
  deepseek-v4-pro（OpenAI 兼容直连），JSON 解析失败重试 1 次，
  仍失败置 needs_review 转教师判断（LLM 故障不阻断录入）。

第二次（E 类复核）：注入该生历史频次 + 班级正确率 → "粗心 vs 未掌握" 判别，
     不覆盖主类别，作为 behavior 附加字段供教师参考。

反标签化（写入 prompt 的【禁止】）：不得对学生个人能力下结论——只描述错误，不评价人。
"""
import re
from typing import Any, Dict, List, Optional

from ..data import db, ontology
from ..providers import llm
from . import options as options_mod

Result = Dict[str, Any]

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


# 判断依据展示给教师，不允许残留内部类别编号（如「错因 C04」「A01/A03」列举）
_EVID_CODE_RE = re.compile(
    r"[（(]?\s*(?:符合)?(?:错因|类别|category[_ ]?id)\s*[:：]?\s*"
    r"[A-E]\d{2}[a-z]?(?:\s*[/、,，]\s*[A-E]\d{2}[a-z]?)*"
    r"\s*(?:的)?(?:典型特征|典型表现)?\s*[）)]?", re.IGNORECASE)


def _clean_evidence(text: str) -> str:
    return _EVID_CODE_RE.sub("", text).strip(" ，,；;（）()：:")


# ---------------------------------------------------------------- DeepSeek

_PROMPT_TEMPLATE = """【角色】你是初中（7-9 年级）英语错因分析专家，只能从给定的 36 类常见错因中选择。
【36 类常见错因】
{ontology_text}
【输入】题目：{question}
学生作答：{answer}
正确答案：{correct}
题型：{qtype}
阅读/完形原文：{passage}
【输出约束】
1. 只输出 JSON：{{"category_id": str, "evidence": str, "teaching_point": str, "confidence": float}}
2. category_id 必须来自上述 36 类，禁止自创类别
3. evidence 必须引用学生作答中的具体内容，用直白的教师能懂的话描述，禁止空泛表述、不要出现"本体库""信号"等技术词、不要出现类别编号（如 A01）
4. 阅读理解题重点甄别 C01-C06（须结合原文定位证据）；书面表达重点甄别 B01-B06 中式英语
5. 置信度 <0.6 时系统将置 needs_review=true
【禁止】不得对学生个人能力下结论（如"基础薄弱"）——只描述错误，不评价人"""


def _deepseek_classify(question: str, answer: str, correct: str, qtype: str,
                       passage: str = "") -> Result:
    """真实归因：统一 LLM 客户端（OpenAI 兼容直连，deepseek-v4-pro）。

    解析失败/类别非法时重试 1 次，仍失败置 needs_review 由教师判断。
    passage 为阅读理解/完形填空的完整原文，供 C 类归因定位证据。
    """
    prompt = _PROMPT_TEMPLATE.format(
        ontology_text=ontology.render_prompt_text(),
        question=_norm(question) or "（未提供）",
        answer=_norm(answer) or "（空白未答）",
        correct=_norm(correct) or "（未提供）",
        qtype=qtype or "未标注",
        passage=(_norm(passage)[:1500] or "（无）"),
    )
    messages = [
        {"role": "system",
         "content": "你是错因归因引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user", "content": prompt},
    ]

    last_err = ""
    for _attempt in range(2):  # 类别非法/解析失败时重试 1 次；网络失败已在 chat 内重试
        try:
            # 第二次为解析层重发，关闭网络层重试，控制总等待预算
            data = llm.chat_json(messages, temperature=0.2,
                                 retries=0 if _attempt else None)
        except llm.LLMError as exc:
            # 网络层已重试仍失败——直接降级待复核，不再叠加等待
            last_err = str(exc)
            break
        if isinstance(data, dict):
            cid = str(data.get("category_id", "")).strip()
            if cid and ontology.get_category(cid):
                try:
                    conf = float(data.get("confidence", 0.5))
                except (TypeError, ValueError):
                    conf = 0.5
                return {
                    "category_id": cid,
                    "evidence": _clean_evidence(str(data.get("evidence", ""))),
                    "teaching_point": str(data.get("teaching_point", "")),
                    "confidence": round(conf, 2),
                    "needs_review": conf < 0.6,
                    "source": "llm",
                }
            last_err = "category_id 非法: %r" % cid
    return {
        "category_id": "",
        "evidence": "大模型调用/解析失败（%s），请教师判断归类" % last_err[:80],
        "teaching_point": "",
        "confidence": 0.3,
        "needs_review": True,
        "source": "llm-error",
    }


# ---------------------------------------------------------------- 对外接口

def classify(question: str, answer: str, correct: str = "",
             qtype: str = "", options: Optional[List[str]] = None,
             passage: str = "") -> Result:
    """第一次调用：逐题归因（deepseek-v4-pro 直连）。

    选择题选项随题干拼入归因文本（错因需结合选项判断，如"误选时态干扰项"）；
    passage 为阅读/完形原文，供 C 类归因定位证据；LLM 调用/解析失败不阻断
    录入——降级为待教师确认（needs_review）。
    """
    q = (question or "").strip()
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if opts:
        q = q + "\n" + "\n".join(opts)
    return _deepseek_classify(q, answer, correct, qtype, passage)


# ---------------------------------------------------------------- 拍照切题

_QTYPES = ("单选", "多选", "判断题", "语法选择", "完形填空", "阅读理解", "口语应用",
           "任务型阅读", "完成句子", "概要补全", "书面表达", "词语运用", "听写", "其他")

_SPLIT_PROMPT_TEMPLATE = """【切题任务】把试卷/书本照片的多页转录文本切分成独立题目。
【规则】
1. 按题号切分（1. 2. …、I. II. …、(1) (2) …等）；页眉、页脚、水印、姓名等非题目内容忽略
2. 每题判定题型：单选/多选/判断题/语法选择/完形填空/阅读理解/口语应用/任务型阅读/完成句子/概要补全/书面表达/词语运用/其他（听力题不切，本系统不处理音频类题目）
3. 选择题选项完整保留到 options（保留 A. B. C. D. 字母前缀）；填空处保留 ____
4. 阅读理解/完形填空的完整原文放入 passage；同一篇文章的多道小题各自成题、共享同一原文；
   完形填空的每一空若单独成小题（如 21-30 空），合并为一题（题干说明 + 各空选项不拆）
5. 跨页（【第N页】标记）的同一题合并为一题，按题号顺序输出
6. 只转录原题，不得改写、翻译或补全内容；无法辨认的部分属原样保留
【转录文本】
{pages_text}
【输出约束】只输出 JSON：{{"questions": [{{"qtype": "单选", "question": "题干全文",
 "options": ["A. …", "B. …"], "passage": "阅读/完形原文，无则为空串"}}]}}"""


def split_questions(pages_text: str) -> List[Dict[str, Any]]:
    """多页 OCR 转录文本 → 题目列表（拍照切题第二步）。

    返回 [{qtype, question, options, passage}]，字段均经规范化；
    LLM 失败/解析失败返回 []（由路由层提示教师改用单题录入）。
    反标签化：仅处理题目文本，无任何学生信息。
    """
    text = (pages_text or "").strip()
    if not text:
        return []
    messages = [
        {"role": "system",
         "content": "你是试卷切题引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user",
         "content": _SPLIT_PROMPT_TEMPLATE.format(pages_text=text[:20000])},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.1)
    except llm.LLMError:
        return []
    if isinstance(data, dict):
        items = data.get("questions") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        q = str(it.get("question") or "").strip()
        if not q:
            continue
        qtype = str(it.get("qtype") or "").strip()
        if qtype not in _QTYPES:
            qtype = "其他"
        # 选项异常检查：重复字母（两个A）、内容重复、字母缺失/错序 → 规范重编号
        opts, opt_issues = options_mod.normalize_options(it.get("options"))
        item = {
            "qtype": qtype,
            "question": q,
            "options": opts,
            "passage": str(it.get("passage") or "").strip(),
        }
        if opt_issues:
            item["option_issues"] = opt_issues
        out.append(item)
    return out


def review_behavior(student_code: str, category_id: str,
                    question: str, class_name: str = "") -> Optional[Dict[str, Any]]:
    """第二次调用：E 类复核——粗心 vs 未掌握判别（不覆盖主类别）。

    变量口径：
      student_cat_count  含本次在内该生该错因出现次数（历史确认记录 + 1）
      class_correct_rate 该题班级正确率的近似口径（1 - 同题干错题人次/班级人数）
      student_other_similar_correct  该生同组其他类别无错误记录视为 true
    """
    if not category_id or category_id.startswith("E") or not student_code:
        return None
    cat = ontology.get_category(category_id)
    if not cat:
        return None

    history = db.student_category_history(student_code, category_id)
    variables = {
        "student_cat_count": history + 1,
        "class_correct_rate": db.class_baseline(question, class_name or None) or 0.5,
        "student_other_similar_correct": history == 0,
    }
    if ontology.eval_e_rule("E05", variables):
        return {
            "tag": "E05",
            "note": "该生该错因已出现 %d 次，或该题班级正确率低——判断为知识未掌握，建议列入个别辅导"
                    % variables["student_cat_count"],
            "student_cat_count": variables["student_cat_count"],
            "class_correct_rate": variables["class_correct_rate"],
        }
    if ontology.eval_e_rule("E02", variables):
        return {
            "tag": "E02",
            "note": "该错因仅出现 1 次且班级正确率 %.0f%%——判断为粗心性失误，建议习惯训练而非重讲"
                    % (variables["class_correct_rate"] * 100),
            "student_cat_count": variables["student_cat_count"],
            "class_correct_rate": variables["class_correct_rate"],
        }
    return None
