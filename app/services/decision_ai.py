"""决策单 AI 讲评策略层（LLM 增强）。

架构定位：规则层（decision.py）输出确定性数据（统计、名单、阈值取舍），
本模块把结构化决策单交给大模型，生成"讲评课执行策略"——
数字由 SQL 保证（不编造），策略与话术由 LLM 提供（灵活通用），教师终审后采用。

反标签化：发给模型的数据仅含错因统计、题型与错例文本、学生代号（无姓名），
输出约束"不评价学生个人、不得编造数字"。
"""
import json
from typing import Any, Dict, List

from ..core import config
from ..providers import llm

_PROMPT = """【角色】你是经验丰富的初中（7-9 年级）英语教研组长。下面是一份由系统按确定性规则
统计生成的"讲评课方案"数据，请为任课教师生成可直接落地的讲评课执行策略。

【讲评方案数据】
{data}

【输出要求】只输出一个 JSON 对象（不要输出其他文字）：
{{
  "overview": "总体判断 2-3 句：本班本次测验的核心问题与讲评课整体思路",
  "timing": "45 分钟课堂时间分配建议，具体到分钟",
  "focus_advice": [
    {{"category_id": "错因编号", "hook": "从典型错例切入的导入话术（1 句，含错例原文引用）",
      "steps": "讲解步骤与方法建议（2-3 句，具体到先做什么后做什么）",
      "board": "板书要点（1 句）"}}
  ],
  "skip_advice": "不讲清单的课后处理建议（1-2 句）",
  "tutor_advice": "个别辅导的组织建议（1-2 句，如面批安排，不涉及学生评价）",
  "risk": "需要提醒教师的 1 个风险点（如某类错因跨单元复发、个别学生需关注）"
}}

【硬性约束】
1. 所有人次、错误率等数字以给定数据为准，禁止编造或改算
2. focus_advice 必须覆盖数据中 focus 的每一个错因编号，不得增删
3. 不对任何学生作评价性表述；语言面向教师同行，务实不空泛"""


def _slim_sheet(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """提取生成策略所需的最小数据集（控制 prompt 体量、剔除内部字段）。"""
    focus: List[Dict[str, Any]] = []
    for f in sheet.get("focus") or []:
        focus.append({
            "category_id": f["category_id"],
            "name": f["name"],
            "count": f["count"],
            "rate": f["rate"],
            "teaching_point": f.get("teaching_point", ""),
            "examples": [
                {"question": e.get("question", ""), "answer": e.get("answer", ""),
                 "qtype": e.get("qtype", "")}
                for e in (f.get("examples") or [])[:2]
            ],
        })
    skip = [{"category_id": s["category_id"], "name": s["name"], "count": s["count"]}
            for s in (sheet.get("skip") or [])[:8]]
    tutors = [{"student_code": t["student_code"], "category_id": t["category_id"],
               "count": t["count"]}
              for t in (sheet.get("tutors") or [])[:15]]
    return {
        "class_name": sheet.get("class_name"),
        "unit": sheet.get("unit"),
        "class_size": sheet.get("class_size"),
        "total_confirmed": sheet.get("total_confirmed"),
        "focus": focus,
        "skip": skip,
        "tutors": tutors,
    }


def generate_strategy(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """基于规则决策单生成 AI 讲评策略；失败显式报错（规则版决策单不受影响）。"""
    if not config.llm_available():
        raise ValueError("LLM 未配置 API key（请检查 .env 的 DEEPSEEK_API_KEY）")
    if not (sheet.get("focus") or sheet.get("skip")):
        raise ValueError("当前筛选条件下没有已确认的错因数据，请先录入并确认错题")
    messages = [
        {"role": "system", "content": "只输出一个 JSON 对象，不输出其他文字。"},
        {"role": "user", "content": _PROMPT.format(
            data=json.dumps(_slim_sheet(sheet), ensure_ascii=False, indent=1))},
    ]
    try:
        text = llm.chat(messages, temperature=0.3)
    except llm.LLMError as exc:
        raise ValueError("AI 生成失败（%s）——请检查网络与 API key 后重试" % exc)
    try:
        data = llm.parse_json(text)
    except Exception:  # noqa: BLE001 —— 解析失败给出可读错误
        raise ValueError("AI 返回格式无法解析，请重试")
    if not isinstance(data, dict) or not data.get("overview"):
        raise ValueError("AI 返回的策略为空，请重试")
    if not isinstance(data.get("focus_advice"), list):
        data["focus_advice"] = []
    return data
