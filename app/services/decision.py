"""讲评课决策单——与市面工具的分水岭。

生成逻辑全部为确定性算法，不依赖大模型：讲什么的取舍是教学专业判断，
规则透明（阈值可配），每个数字可追溯；AI 只负责归因与练习生成，教师负责终审。

输出三段（对齐方案 §4.3 决策单样例）：
  ① 本课重点讲（TOP≤3 错因 + 人次/典型错例/讲解要点/配套练习/个别辅导数）
  ② 本课不讲清单（错误率 < SKIP_RATE，建议个别反馈解决）
  ③ 个别辅导名单（同类错因 ≥ TUTOR_MIN 次；仅供教师教学参考，不构成学生评价）

支持按班级生成（class_name=None 时合并全部班级）。
反标签化：名单固定提示语硬编码于输出，前端与打印页不可省略。
"""
from typing import Any, Dict, List, Optional

from ..data import db, ontology

# 阈值（可配）
FOCUS_TOP = 3          # 重点讲错因上限（贪多则无效）
FOCUS_MIN_COUNT = 3    # 进入"重点讲"的人次下限
SKIP_RATE = 0.10       # 错误率低于该值 → 不讲清单（count / class_size）
SKIP_MIN_COUNT = 2     # 绝对人次下限：≤ 该人次一律进不讲清单（班级规模自适应）
                       # 说明：SKIP_RATE 按 45-50 人班标定，小班（如 10 人）时
                       # "1 人次" 已占 10%，比率口径失灵 → 不讲清单永远为空。
                       # 增加绝对人次判据后，大班沿用比率、小班按人次，语义统一为
                       # "不足以构成班级共性问题 → 个别反馈解决，不占课堂时间"。
TUTOR_MIN = 4          # 同一学生同类错因达该次数 → 个别辅导名单
                       # （3 次可能巧合或已随讲评化解，4 次明确指向个体干预；
                       #  也与班级级 FOCUS_MIN_COUNT=3 拉开层级，避免名单过半）
DEFAULT_CLASS_SIZE = 45  # 未导入学生名单时的默认班级规模

NOTICE = "本名单为教学提示，不构成学生评价；不下发学生与家长，不作排名使用。"


def _display_name(category_id: str) -> str:
    return ontology.category_name(category_id)


def _typical_examples(category_id: str, unit: Optional[str],
                      class_name: Optional[str], exam_type: Optional[str],
                      batch_nos: Optional[List[str]] = None,
                      date_from: Optional[str] = None,
                      date_to: Optional[str] = None,
                      limit: int = 2) -> List[Dict[str, Any]]:
    """典型错例：该类别教师确认后的最近记录（question / answer / evidence）。"""
    errors = db.list_errors(confirmed=True, unit=unit, class_name=class_name,
                            exam_type=exam_type, batch_nos=batch_nos,
                            date_from=date_from, date_to=date_to, limit=200)
    picked: List[Dict[str, Any]] = []
    for e in errors:
        if (e.get("teacher_override") or e.get("category_id")) != category_id:
            continue
        picked.append({
            "student_code": e["student_code"],
            "question": e["question"][:80],
            "answer": (e.get("answer") or "")[:80],
            "evidence": (e.get("evidence") or "")[:120],
            "qtype": e.get("qtype") or "",
            "passage": (e.get("passage") or "")[:400],
        })
        if len(picked) >= limit:
            break
    return picked


def _scope_label(class_name: Optional[str], exam_type: Optional[str],
                 batch_nos: Optional[List[str]],
                 date_from: Optional[str], date_to: Optional[str]) -> str:
    """人读的数据范围描述（决策单头部展示）：班级 · 任务 · 批次 · 阶段。"""
    parts = []
    if class_name:
        parts.append(class_name)
    if exam_type:
        parts.append(exam_type)
    if batch_nos:
        parts.append("、".join(batch_nos))
    if date_from or date_to:
        parts.append("%s ~ %s" % (date_from or "起", date_to or "今"))
    return " · ".join(parts) if parts else "全部历史数据"


def build_decision_sheet(unit: Optional[str] = None,
                         class_name: Optional[str] = None,
                         exam_type: Optional[str] = None,
                         batch_nos: Optional[List[str]] = None,
                         date_from: Optional[str] = None,
                         date_to: Optional[str] = None,
                         save: bool = True) -> Dict[str, Any]:
    stats = db.category_stats(unit, class_name, exam_type,
                              batch_nos=batch_nos,
                              date_from=date_from, date_to=date_to)  # 教师确认后口径，按人次降序
    size = db.class_size(class_name) or DEFAULT_CLASS_SIZE
    total = db.total_confirmed(unit, class_name, exam_type,
                               batch_nos=batch_nos,
                               date_from=date_from, date_to=date_to)

    focus_ids = [s["category_id"] for s in stats if s["count"] >= FOCUS_MIN_COUNT][:FOCUS_TOP]

    focus: List[Dict[str, Any]] = []
    for s in stats:
        cid = s["category_id"]
        if cid not in focus_ids:
            continue
        cat = ontology.get_category(cid) or {}
        approved = [p for p in db.list_practices(status="approved")
                    if p["category_id"] == cid]
        focus.append({
            "category_id": cid,
            "name": _display_name(cid),
            "count": s["count"],
            "rate": round(s["count"] / size, 4),
            "examples": _typical_examples(cid, unit, class_name, exam_type,
                                           batch_nos=batch_nos,
                                           date_from=date_from, date_to=date_to),
            "teaching_point": cat.get("teaching_point", ""),
            "approved_practice_count": len(approved),
            "practice_hint": "已有 %d 套已审校练习" % len(approved) if approved
                             else "尚无已审校练习，可生成（AI 生成需审校后附入）",
        })

    skip: List[Dict[str, Any]] = []
    for s in stats:
        cid = s["category_id"]
        if cid in focus_ids:
            continue
        rate = s["count"] / size
        if rate < SKIP_RATE or s["count"] <= SKIP_MIN_COUNT:
            skip.append({
                "category_id": cid,
                "name": _display_name(cid),
                "count": s["count"],
                "rate": round(rate, 4),
                "advice": "采用个别批改反馈解决，不占用课堂讲评时间",
            })

    tutors: List[Dict[str, Any]] = []
    for row in db.student_category_counts(unit, class_name, exam_type,
                                          batch_nos=batch_nos,
                                          date_from=date_from, date_to=date_to):
        if row["count"] >= TUTOR_MIN:
            cat = ontology.get_category(row["final_cat"]) or {}
            advice = cat.get("teaching_point", "结合该生近期作业面批确认")
            if row["final_cat"].startswith("C"):
                advice = "建议面批 2 道题的推理/定位过程：" + advice
            tutors.append({
                "student_code": row["code"],
                "category_id": row["final_cat"],
                "name": _display_name(row["final_cat"]),
                "count": row["count"],
                "advice": advice,
            })
    tutors.sort(key=lambda t: (-t["count"], t["student_code"]))

    scope_parts = []
    if class_name:
        scope_parts.append("class:%s" % class_name)
    if exam_type:
        scope_parts.append("task:%s" % exam_type)
    if batch_nos:
        scope_parts.append("batch:%s" % ",".join(batch_nos))
    if date_from or date_to:
        scope_parts.append("range:%s~%s" % (date_from or "", date_to or ""))
    if not scope_parts:
        scope_parts.append("task:all")

    sheet: Dict[str, Any] = {
        "scope": "|".join(scope_parts),
        "scope_label": _scope_label(class_name, exam_type, batch_nos,
                                     date_from, date_to),
        "unit": unit,
        "exam_type": exam_type,
        "batch_nos": batch_nos or [],
        "date_from": date_from,
        "date_to": date_to,
        "class_name": class_name,
        "class_size": size,
        "total_confirmed": total,
        "focus": focus,
        "skip": skip,
        "tutors": tutors,
        "notice": NOTICE,
    }
    if save:
        sheet["sheet_id"] = db.save_decision_sheet(sheet)
    return sheet
