"""讲评课决策单：阈值、不讲清单、个别辅导名单、快照留档、反标签化提示。"""
from app import db, decision


def _seed_errors(rows):
    """rows: [(student, cat, exam_type)]，全部教师确认。"""
    for student, cat, exam_type in rows:
        eid = db.insert_error({
            "student_code": student, "question": "Q %s %s" % (cat, exam_type),
            "answer": "wrong", "correct": "right", "qtype": "单选",
            "exam_type": exam_type, "category_id": cat, "evidence": "e",
            "confidence": 0.9,
        })
        db.confirm_error(eid, "accept")


def _seed_class(n=40):
    db.import_students([{"code": "S%02d" % i} for i in range(1, n + 1)])


def test_focus_skip_tutors_match_manual_expectation(isolated_db):
    """构造 30 条错题 → 决策单三段与手工预期一致（对齐框架 S2 验收标准）。"""
    _seed_class(40)
    rows = []
    # B01 时态: 14 人次（重点讲）；B02 被动: 9 人次（重点讲）；C03 推理: 7 人次（重点讲）
    for i in range(14):
        rows.append(("S%02d" % (i + 1), "B01", "课时作业"))
    for i in range(9):
        rows.append(("S%02d" % (i + 1), "B02", "课时作业"))
    for i in range(7):
        rows.append(("S%02d" % (i + 1), "C03", "课时作业"))
    # 不讲清单：A01 拼写 3 人次（3/40=7.5% < 10%）
    for i in range(3):
        rows.append(("S%02d" % (i + 30), "A01", "课时作业"))
    # S07 的 B01 已 1 次，再补 2 次不同任务类型？同 exam_type 统计下 S07 B01 只有 1 次 → 不入名单
    # S05 B02 已 1 次；给 S05 再加 B02 三次（同任务类型）→ 4 次（TUTOR_MIN=4）→ 个别辅导
    for _ in range(3):
        rows.append(("S05", "B02", "课时作业"))
    # 未确认记录不参与统计
    eid = db.insert_error({
        "student_code": "S01", "question": "pending", "answer": "x", "correct": "y",
        "qtype": "单选", "exam_type": "课时作业", "category_id": "A03",
        "evidence": "e", "confidence": 0.9})
    # ignore 的记录也不参与
    eid2 = db.insert_error({
        "student_code": "S02", "question": "ignored", "answer": "x", "correct": "y",
        "qtype": "单选", "exam_type": "课时作业", "category_id": "B07",
        "evidence": "e", "confidence": 0.9})
    db.confirm_error(eid2, "ignore")
    _seed_errors(rows)

    sheet = decision.build_decision_sheet(exam_type="课时作业")

    # ① 重点讲：恰好 B01/B02/C03，按人次降序，不超过 3 个
    focus_ids = [f["category_id"] for f in sheet["focus"]]
    assert focus_ids == ["B01", "B02", "C03"]
    assert sheet["focus"][0]["count"] == 14
    assert sheet["focus"][0]["examples"], "重点错因必须带典型错例"
    assert "时态" in sheet["focus"][0]["name"]

    # ② 不讲清单：A01（3/40 < 10%）；A03（未确认）与 B07（ignored）不得出现
    skip_ids = [s["category_id"] for s in sheet["skip"]]
    assert "A01" in skip_ids
    assert "A03" not in skip_ids and "B07" not in skip_ids

    # ③ 个别辅导：S05 B02 4 次（TUTOR_MIN=4）
    tutor_keys = [(t["student_code"], t["category_id"]) for t in sheet["tutors"]]
    assert ("S05", "B02") in tutor_keys
    assert ("S07", "B01") not in tutor_keys  # S07 仅 1 次

    # ④ 反标签化固定提示语
    assert "不构成学生评价" in sheet["notice"]

    # ⑤ 快照留档
    sheets = isolated_db.list_decision_sheets()
    assert any(s["scope"] == "task:课时作业" for s in sheets)


def test_teacher_override_changes_statistics(isolated_db):
    """教师改判优先：AI 判 B01 被改为 A03 后，统计按 A03 计。"""
    _seed_class(40)
    for i in range(4):
        eid = db.insert_error({
            "student_code": "S%02d" % (i + 1), "question": "q%d" % i,
            "answer": "a", "correct": "c", "qtype": "单选", "exam_type": "课时作业",
            "category_id": "B01", "evidence": "e", "confidence": 0.9})
        db.confirm_error(eid, "modify", override="A03")
    stats = {s["category_id"]: s["count"] for s in isolated_db.category_stats(None, None, "课时作业")}
    assert stats.get("A03") == 4 and "B01" not in stats


def test_empty_scope_returns_empty_sheet(isolated_db):
    sheet = decision.build_decision_sheet(exam_type="不存在的任务类型")
    assert sheet["focus"] == [] and sheet["skip"] == [] and sheet["tutors"] == []
    assert "不构成学生评价" in sheet["notice"]
