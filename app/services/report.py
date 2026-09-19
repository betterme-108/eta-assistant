"""报告聚合：看板、错因演变曲线、预警、教师修正率（护AI 核心数据）。"""
from typing import Any, Dict, List, Optional

from ..data import db, ontology

ALERT_RISE_RATE = 0.5   # 周环比上升超过 50%
ALERT_MIN_COUNT = 3     # 且最新人次 ≥3 才预警


def dashboard(unit: Optional[str] = None,
              class_name: Optional[str] = None,
              exam_type: Optional[str] = None,
              batch_no: Optional[str] = None) -> Dict[str, Any]:
    """错因分析总览：batch_no 可选按某次作业/考试过滤（分析页批次维度）。"""
    bn = [batch_no] if batch_no else None
    stats = db.category_stats(unit, class_name, exam_type, batch_nos=bn)
    top = [
        {
            "category_id": s["category_id"],
            "name": ontology.category_name(s["category_id"]),
            "count": s["count"],
        }
        for s in stats[:10]
    ]
    counts = db.scope_counts(unit, class_name, exam_type, batch_nos=bn)
    return {
        "unit": unit,
        "class_name": class_name,
        "exam_type": exam_type,
        "batch_no": batch_no,
        "total_errors": counts["total"],
        "pending_review": counts["pending"],
        "confirmed": db.total_confirmed(unit, class_name, exam_type, batch_nos=bn),
        "students_involved": counts["students"],
        "top_categories": top,
        "weekly_trend": db.weekly_trend(class_name=class_name, exam_type=exam_type,
                                        batch_nos=bn),
        "alerts": _alerts(class_name, bn),
    }


def _alerts(class_name: Optional[str] = None,
            batch_nos: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """预警：某错因最近一个统计周较上一周上升 ≥50% 且人次 ≥3。"""
    matrix = db.trend_matrix(by="week", class_name=class_name, batch_nos=batch_nos)
    alerts: List[Dict[str, Any]] = []
    for series in matrix["series"]:
        points = series["points"]
        if len(points) < 2:
            continue
        prev, last = points[-2], points[-1]
        if prev["count"] == 0:
            continue
        rise = (last["count"] - prev["count"]) / prev["count"]
        if rise >= ALERT_RISE_RATE and last["count"] >= ALERT_MIN_COUNT:
            alerts.append({
                "category_id": series["category_id"],
                "name": ontology.category_name(series["category_id"]),
                "from": prev["x"], "to": last["x"],
                "from_count": prev["count"], "to_count": last["count"],
                "rise": round(rise, 4),
                "message": "%s 从 %s 的 %d 人次升至 %s 的 %d 人次（+%d%%），建议关注"
                           % (ontology.category_name(series["category_id"]),
                              prev["x"], prev["count"], last["x"], last["count"],
                              round(rise * 100)),
            })
    return alerts


def trends(by: str = "week", class_name: Optional[str] = None,
          batch_no: Optional[str] = None) -> Dict[str, Any]:
    """错因演变曲线（按录入时间分桶：week 周 / month 月 / half_year 半年），类别名可直接渲染。

    series 按总量降序：前端只绘前 6 条，排序后展示的恰是重点错因；
    batch_no 可选按某次作业/考试过滤。
    """
    if by not in ("week", "month", "half_year"):
        by = "week"
    matrix = db.trend_matrix(by=by, class_name=class_name,
                             batch_nos=[batch_no] if batch_no else None)
    for series in matrix["series"]:
        series["name"] = ontology.category_name(series["category_id"])
        series["_total"] = sum(p["count"] for p in series["points"])
    matrix["series"].sort(key=lambda s: -s["_total"])
    for series in matrix["series"]:
        del series["_total"]
    matrix["overrides"] = db.override_stats(class_name)
    return matrix
