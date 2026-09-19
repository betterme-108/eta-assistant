"""报表口径测试：看板统计卡（总数/待确认/涉及学生/周趋势）必须按任务类型与
class_name 同步过滤；趋势按录入错题时间分桶（周/月/半年）。

防回归：曾出现选定范围后统计卡仍显示全库数字的口径错误（v3 修复，
scope_counts / weekly_trend / trend_matrix 统一过滤）。
"""
from app import db, report


def _insert(student, cat, exam_type, batch_no, class_name="", confirm=True):
    eid = db.insert_error({
        "student_code": student, "class_name": class_name,
        "question": "Q %s %s" % (cat, exam_type),
        "answer": "wrong", "correct": "right", "qtype": "单选",
        "exam_type": exam_type, "batch_no": batch_no, "category_id": cat,
        "evidence": "e", "confidence": 0.9,
    })
    if confirm:
        db.confirm_error(eid, "accept")
    return eid


def test_dashboard_counts_follow_exam_type_filter(isolated_db):
    db.import_students([{"code": "S%02d" % i} for i in range(1, 11)])
    for i in range(6):
        _insert("S%02d" % (i + 1), "A01", "课时作业", "1单元2课时")
    for i in range(4):
        _insert("S%02d" % (i + 7), "B01", "考试试卷", "期中考试")
    _insert("S01", "A03", "课时作业", "1单元2课时", confirm=False)  # 未确认

    d_all = report.dashboard()
    assert d_all["total_errors"] == 11
    assert d_all["pending_review"] == 1

    d1 = report.dashboard(exam_type="课时作业")
    assert d1["total_errors"] == 7          # 修复前错误地返回全库 11
    assert d1["pending_review"] == 1
    assert d1["confirmed"] == 6
    assert d1["students_involved"] == 6     # S01-S06

    d2 = report.dashboard(exam_type="考试试卷")
    assert d2["total_errors"] == 4
    assert d2["pending_review"] == 0
    assert d2["students_involved"] == 4


def test_dashboard_counts_follow_class_filter(isolated_db):
    db.import_students([
        {"code": "91%02d" % i, "class": "九(1)班"} for i in range(1, 4)
    ] + [
        {"code": "93%02d" % i, "class": "九(3)班"} for i in range(1, 4)
    ])
    for i in range(1, 4):
        _insert("91%02d" % i, "A01", "课时作业", "1单元2课时", class_name="九(1)班")
    for i in range(1, 4):
        _insert("93%02d" % i, "C03", "考试试卷", "期中考试", class_name="九(3)班")

    d = report.dashboard(class_name="九(1)班")
    assert d["total_errors"] == 3
    assert d["students_involved"] == 3
    assert [t["category_id"] for t in d["top_categories"]] == ["A01"]

    both = report.dashboard()
    assert both["total_errors"] == 6


def test_weekly_trend_follows_exam_type_filter(isolated_db):
    _insert("S01", "A01", "课时作业", "1单元2课时")
    _insert("S02", "A01", "课时作业", "1单元2课时")
    _insert("S03", "B01", "考试试卷", "期中考试")

    all_weeks = {w["week"]: w["count"] for w in db.weekly_trend()}
    assert sum(all_weeks.values()) == 3
    assert all("周" in k for k in all_weeks)  # 周标签：2026-第N周

    a1_weeks = {w["week"]: w["count"] for w in db.weekly_trend(exam_type="课时作业")}
    assert sum(a1_weeks.values()) == 2       # 修复前考试试卷的记录也会混入


def test_trends_time_buckets(isolated_db):
    _insert("S01", "A01", "课时作业", "1单元2课时")
    _insert("S02", "A01", "课时作业", "1单元2课时")
    _insert("S03", "B01", "考试试卷", "期中考试")

    trend_week = report.trends("week")
    assert trend_week["by"] == "week"
    assert any(s["category_id"] == "A01" for s in trend_week["series"])
    assert len(trend_week["x_labels"]) == 1 and "周" in trend_week["x_labels"][0]

    # 月 / 半年分桶同样可渲染（x 为录入时间分桶标签）
    assert report.trends("month")["by"] == "month"
    assert report.trends("half_year")["by"] == "half_year"
    # 非法 by 回退为 week
    assert report.trends("unit")["by"] == "week"
