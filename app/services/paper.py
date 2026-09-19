"""AI 组卷：基于班级/单元的已确认错题分布生成综合试卷（教师审校后导出）。

与补偿练习（practice.py）的分野：
  练习面向单一错因的定向训练，组卷面向错题整体的混合测试——
  题量按错因人次数加权覆盖（人次多的错因多出题），每题标注针对的错因编号。

合规要点：AI 生成的试卷一律 status=draft，须经教师审校（approve）
后方可导出使用；出参标注 "AI 生成·待审校"。
"""
from typing import Any, Dict

from ..core import config
from ..data import db, ontology
from .practice import _date_scope_label, _llm_generate

_DIFFICULTY_TEXT = {
    "easy": "基础巩固：直接考查基本考点，语境简单，避免长难句与强干扰项（适配 7-8 年级水平）",
    "mid": "标准难度：贴近初中常考题型，含少量综合运用（适配 8-9 年级水平）",
    "hard": "适度挑战：接近中考难度，含综合语境运用与易混干扰项（适配九年级）",
}
_QTYPE_TEXT = {
    "mc": "以单项选择为主（约占八成），其余穿插改错/完成句子",
    "read": "以完形填空与阅读理解为主（合计约占七成，需配短文），其余单选",
    "write": "以改错、完成句子与书面表达为主（合计约占七成），其余单选",
}

_PAPER_PROMPT = """【角色】你是初中（7-9 年级）英语教师，根据班级错题记录出一份综合测试卷。
【错题概况】（错因编号 · 名称 · 人次 · 教学要点）
{focus}
【难度】{difficulty}
【题型构成】{qtypes}；同类型题目连续排列
【要求】
1. 出一份覆盖上述错因的试卷：优先覆盖人次多的错因（题量大致按人次分配），
   人次最少的错因至少 1 题
2. 只输出 JSON 数组：[{{"type": "单选", "q": "...", "options": ["A内容","B内容",...], "answer": "B", "explanation": "...", "category": "A01"}}]
   改错题省略 options；category 填该题针对的错因编号（从上面给出的编号中选）
3. 解析须点明考点；词汇与语法严格限定在初中 7-9 年级课标范围内，不得出现高中知识"""


def generate_paper(n: int = 10, unit: str = "", class_name: str = "",
                   title: str = "", difficulty: str = "",
                   qtype_pref: str = "", exam_type: str = "",
                   date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    """基于错题统计 AI 组卷（draft 状态），返回含 paper_id 供审校与导出。

    错题来源范围：class_name（班级）+ exam_type（任务类型：课时作业/考试试卷）
    + date_from/date_to（阶段，留空 = 全部历史，按录入时间筛选）；
    unit 为旧参数兼容保留。difficulty（easy/mid/hard）与 qtype_pref（mc/read/write）
    留空时自动适配。
    """
    if not config.llm_available():
        raise ValueError("LLM 未配置 API key（请检查 .env 的 DEEPSEEK_API_KEY）")
    stats = db.category_stats(unit or None, class_name or None, exam_type or None,
                              date_from=date_from or None,
                              date_to=date_to or None)  # 教师确认口径
    if not stats:
        raise ValueError("当前范围没有已确认的错题，请先录入并确认错题再组卷")

    focus_lines = []
    for s in stats[:8]:   # 人次降序前 8 类，覆盖绝大多数错题
        cat = ontology.get_category(s["category_id"]) or {}
        focus_lines.append("%s %s · %d 人次 · %s"
                           % (s["category_id"], cat.get("name", ""),
                              s["count"], (cat.get("teaching_point") or "")[:50]))
    prompt = _PAPER_PROMPT.format(
        focus="\n".join(focus_lines),
        difficulty=_DIFFICULTY_TEXT.get(difficulty, "标准难度：贴近初中常考题型，适配 7-9 年级学段"),
        qtypes=_QTYPE_TEXT.get(qtype_pref, "自动混合：单选为主，穿插改错/完成句子等"),
    )
    items = _llm_generate(prompt, n)
    for it in items:
        it["ai_tag"] = "AI 生成·待审校"  # 合规标注

    scope_parts = [p for p in (class_name, exam_type or unit,
                               _date_scope_label(date_from, date_to)) if p]
    auto_title = title or "%s综合测试卷" % (class_name + " · " if class_name else "")
    pid = db.insert_paper(auto_title, items,
                          class_name=class_name or "", unit=unit or "",
                          scope=" · ".join(scope_parts) or "全部错题")
    return {"paper_id": pid, "title": auto_title, "scope": " · ".join(scope_parts) or "全部错题",
            "source": "llm", "items": items}


def approve(paper_id: int) -> bool:
    """教师审校通过后才能导出使用。"""
    return db.approve_paper(paper_id)
