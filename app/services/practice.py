"""补偿练习生成 + 教师审校。

合规要点（《教师生成式人工智能应用指引》"加强内容审查把关"）：
  AI 生成的练习一律 status=draft，必须经教师审校（approve）后方可附入讲评单。

deepseek-v4-pro 按类别教学要点生成 n 题；生成失败显式报错（不降级）。
所有 AI 生成内容在出参中标注 "AI 生成·待审校"。
"""
import json
import re
from typing import Any, Dict, List

from ..core import config
from ..data import db, ontology
from ..providers import llm

Item = Dict[str, Any]

# ---------------------------------------------------------------- DeepSeek 生成

_GEN_PROMPT = """【角色】你是初中（7-9 年级）英语教师，为某一类错因设计补偿练习。
【错因类别】{cid} {name}
【判定信号】{signals}
【教学要点】{teaching_point}
【示例】{examples}
【班级真实错例】{scope_note}（练习设计优先针对这些真实错误，语境与难度向它们靠拢）
{real_examples}
【要求】
1. 生成适合初中 7-9 年级学生的练习（单选/改错优先），覆盖该错因的核心判定信号
2. 只输出 JSON 数组：[{{"type": "单选", "q": "...", "options": ["A内容","B内容",...], "answer": "B", "explanation": "..."}}]
   改错题省略 options
3. 题目语言难度适配初中水平，词汇与语法严格限定在 7-9 年级课标范围内，解析须点明考点
4. 若该错因属于阅读理解类（C 开头）：生成 1 篇短文（约 120 词）配 2-3 题，
   短文放入每题的 passage 字段（同一篇），q 为具体问题；若属于完形填空相关错因，
   生成的完形题同样把带空原文放入 passage，q 指明第几空"""

# 单次 LLM 调用的题数上限（输出 token 限制；超过则分批生成并在批间防重复）
_BATCH_SIZE = 10


def _extract_items(text: str, batch: int) -> List[Item]:
    """从 LLM 输出提取题目（容错）：整体 JSON 解析 → 截断时逐题对象正则提取。

    输出超过 max_tokens 被截断时 json.loads 会失败；正则按单个题目对象
    （题面不含嵌套花括号）逐段恢复，能救回已完整输出的题目，避免
    「请求 N 题却只入库前 10 题」。
    """
    data = llm.parse_json(text)
    if isinstance(data, dict):
        data = data.get("items") or data.get("data") or []
    if isinstance(data, list):
        return [it for it in data
                if isinstance(it, dict) and it.get("q") and it.get("answer")][:batch]
    out: List[Item] = []
    for m in re.finditer(r"\{[^{}]*\}", text or ""):
        try:
            it = json.loads(m.group())
        except json.JSONDecodeError:
            continue
        if isinstance(it, dict) and it.get("q") and it.get("answer"):
            out.append(it)
        if len(out) >= batch:
            break
    return out


def _llm_generate(base_prompt: str, n: int) -> List[Item]:
    """LLM 分批生成题目：base_prompt 不含数量与已有题；批间附已有题干防重复；失败显式报错。

    补偿练习与组卷共用（题干去重、passage 保留、空结果报错的行为一致）。
    """
    items: List[Item] = []
    seen = set()
    calls = 0
    empty_streak = 0
    while len(items) < n and calls < 24:  # 上限防死循环（100 题 ≈ 10 批，余量充足）
        batch = min(_BATCH_SIZE, n - len(items))
        existing = "\n".join("%d. %s" % (i + 1, it["q"][:40]) for i, it in enumerate(items)) or "（无）"
        prompt = ("%s\n\n【本次生成】%d 道。\n【不要与下列已有题目重复】（换考点角度/换语境）：\n%s"
                  % (base_prompt, batch, existing))
        messages = [
            {"role": "system", "content": "只输出一个 JSON 数组，不输出其他文字。"},
            {"role": "user", "content": prompt},
        ]
        calls += 1
        # 空批/全重复时换温度重试一次（模型偶发输出偏差），连续两批仍空才收束
        temperature = 0.4 if empty_streak == 0 else 0.6
        try:
            text = llm.chat(messages, temperature=temperature)
            fresh = _extract_items(text, batch)
        except llm.LLMError as exc:
            raise ValueError("AI 生成失败（%s）——请检查网络与 API key 后重试" % exc)
        # 按题干去重累积；阅读理解/完形题保留 passage（题型差异化呈现）
        added = 0
        for it in fresh:
            q = str(it.get("q", "")).strip()
            if not q or q in seen:
                continue
            seen.add(q)
            if it.get("passage") and not isinstance(it["passage"], str):
                it["passage"] = str(it["passage"])
            items.append(it)
            added += 1
        empty_streak = 0 if added else empty_streak + 1
        if empty_streak >= 2:  # 连续两批拿不到新题：继续请求只会浪费调用——提前收束
            break
    if not items:
        raise ValueError("AI 返回的题目为空或格式不含题目，请重试")
    return items[:n]


def _date_scope_label(date_from: str = "", date_to: str = "") -> str:
    """阶段范围 → 人读标签（留空返空串 = 全部历史）。"""
    if not (date_from or date_to):
        return ""
    return "%s ~ %s" % (date_from or "起", date_to or "今")


def _real_example_lines(category_id: str, class_name: str,
                        date_from: str = "", date_to: str = "",
                        limit: int = 5) -> List[str]:
    """该类在该班级/阶段内的已确认错题（教师改判优先口径）→ 真实错例行。

    注入提示词让练习贴合班级实际；范围内没有时返回空列表（退回本体库通用示例）。
    """
    rows = db.list_errors(confirmed=True, class_name=class_name or None,
                          category_id=category_id,
                          date_from=date_from or None, date_to=date_to or None,
                          limit=limit)
    lines = []
    for r in rows:
        q = " ".join((r.get("question") or "").split())[:120]
        a = (r.get("answer") or "（空白）")[:60]
        lines.append("题干：%s｜学生错例：%s" % (q, a))
    return lines


def _deepseek_generate(category: Dict[str, Any], n: int,
                       real_lines: List[str] = None,
                       scope_note: str = "") -> List[Item]:
    """按错因类别构造提示词后交给通用分批生成。"""
    reals = real_lines or []
    base_prompt = _GEN_PROMPT.format(
        cid=category["id"], name=category["name"],
        signals="；".join(category["signals"][:3]),
        teaching_point=category["teaching_point"],
        examples=json.dumps(category["examples"][:2], ensure_ascii=False),
        scope_note=scope_note or "（全部历史，按录入时间）",
        real_examples="\n".join(reals) or "（该范围内暂无已确认错题，按类别通用要点生成）",
    )
    return _llm_generate(base_prompt, n)


# ---------------------------------------------------------------- 对外接口

def generate(category_id: str, n: int = 3,
             class_name: str = "",
             date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    """生成补偿练习（draft 状态），返回含 practice_id 供审校。

    date_from/date_to 圈定错题来源阶段：范围内的真实错例注入提示词，
    scope 标签随练习留档（列表可追溯生成口径）。
    """
    category = ontology.get_category(category_id)
    if not category:
        raise ValueError("类别 %r 不在本体库中" % category_id)

    if not config.llm_available():
        raise ValueError("LLM 未配置 API key（请检查 .env 的 DEEPSEEK_API_KEY）")
    scope = _date_scope_label(date_from, date_to)
    reals = _real_example_lines(category_id, class_name, date_from, date_to)
    items = _deepseek_generate(
        category, n, real_lines=reals,
        scope_note="（%s，按录入时间）" % scope if scope else "")
    source = "llm"
    for it in items:
        it["ai_tag"] = "AI 生成·待审校"  # 合规标注

    pid = db.insert_practice(category_id, items, class_name=class_name, scope=scope)
    return {"practice_id": pid, "category_id": category_id,
            "category_name": category["name"], "source": source,
            "scope": scope, "real_example_n": len(reals), "items": items}


# 多类一次生成的类别上限（生成耗时与审校负担的平衡）
MAX_CATEGORIES = 6


def generate_batch(category_ids: List[str], n: int = 3,
                   class_name: str = "",
                   date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    """多类一次生成：每类各成一套（决策单按类别引用练习的语义不变），逐套审校。"""
    ids = list(dict.fromkeys(category_ids))   # 去重保序
    if not ids:
        raise ValueError("请至少选择一个错因类别")
    if len(ids) > MAX_CATEGORIES:
        raise ValueError("一次最多选 %d 个类别（当前 %d 个）——分次生成更稳妥"
                         % (MAX_CATEGORIES, len(ids)))
    generated = [generate(cid, n, class_name, date_from, date_to) for cid in ids]
    return {"generated": generated, "count": len(generated),
            "total_items": sum(len(g["items"]) for g in generated)}


def approve(practice_id: int) -> bool:
    """教师审校通过后才能附入讲评决策单。"""
    return db.approve_practice(practice_id)
