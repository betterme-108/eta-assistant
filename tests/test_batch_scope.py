"""批次维度与阶段范围测试：错题确认 / 错因分析 / 讲评备课共用的时间线筛选。

覆盖三层：
  db 层     list_batches 批次列表、batch_nos 多选（旧数据 unit 回落兼容）、
            date_from/date_to 阶段范围（date_to 闭区间到当天末）、批量确认按批次
  service  层 decision 决策单范围参数（scope_label 人读描述 + 回显字段）
  api 层    /errors/batches、/decision-sheet、/dashboard、/trends/categories、
            /errors/confirm-bulk、/decision-sheet/export 的批次/阶段参数
提示词回归：印刷已给（题干）与学生补写（作答）必须在三处解析提示词中严格区分。
"""
from fastapi.testclient import TestClient

from app import db, decision
from app.main import app

client = TestClient(app)


def _insert(student, cat, exam_type, batch_no, class_name="", created_at=None,
            unit=None, confirm=True, confidence=0.9):
    eid = db.insert_error({
        "student_code": student, "class_name": class_name,
        "question": "Q %s %s" % (cat, batch_no or unit),
        "answer": "wrong", "correct": "right", "qtype": "单选",
        "exam_type": exam_type, "batch_no": batch_no, "unit": unit,
        "category_id": cat, "evidence": "e", "confidence": confidence,
        "created_at": created_at,
    })
    if confirm:
        db.confirm_error(eid, "accept")
    return eid


def _seed_class(codes):
    db.import_students([{"code": c} for c in codes])


# ---------------------------------------------------------------- db 层

def test_list_batches(isolated_db):
    """批次列表：唯一批次聚合（count/confirmed）、按时间倒序、班级过滤。"""
    _seed_class(["S01", "S02", "S03"])
    _insert("S01", "B01", "课时作业", "Unit 1", created_at="2025-09-26T10:00:00")
    _insert("S02", "B01", "课时作业", "Unit 1", created_at="2025-09-26T11:00:00")
    eid = _insert("S03", "A01", "课时作业", "Unit 1",
                  created_at="2025-09-26T12:00:00", confirm=False)
    _insert("S01", "C03", "考试试卷", "期中考试", created_at="2025-11-07T09:00:00")

    batches = db.list_batches()
    assert [b["batch_no"] for b in batches] == ["期中考试", "Unit 1"]  # 时间倒序
    u1 = batches[1]
    assert u1["exam_type"] == "课时作业"
    assert u1["count"] == 3 and u1["confirmed"] == 2
    assert u1["first_at"] == "2025-09-26T10:00:00"
    assert u1["last_at"] == "2025-09-26T12:00:00"
    db.confirm_error(eid, "accept")
    assert db.list_batches()[1]["confirmed"] == 3

    # 班级过滤
    _insert("T01", "B01", "课时作业", "Unit 9", class_name="九(3)班",
            created_at="2025-12-01T09:00:00")
    assert [b["batch_no"] for b in db.list_batches("九(3)班")] == ["Unit 9"]
    assert len(db.list_batches()) == 3


def test_batch_nos_filter_legacy_unit_fallback(isolated_db):
    """旧数据只有 unit 没有 batch_no：批次筛选经 COALESCE 同样命中。"""
    _seed_class(["S01", "S02"])
    _insert("S01", "B01", "课时作业", None, unit="1单元2课时",
            created_at="2025-10-01T09:00:00")          # 旧数据：batch_no 为空
    _insert("S02", "B01", "课时作业", "Unit 2",
            created_at="2025-10-17T09:00:00")          # 新数据

    rows = db.category_stats(batch_nos=["1单元2课时"])
    assert sum(r["count"] for r in rows) == 1
    both = db.category_stats(batch_nos=["1单元2课时", "Unit 2"])
    assert sum(r["count"] for r in both) == 2
    errs = db.list_errors(batch_nos=["1单元2课时"])
    assert len(errs) == 1 and errs[0]["unit"] == "1单元2课时"


def test_date_range_inclusive_of_end_day(isolated_db):
    """阶段范围闭区间：date_to 当天晚些时候录入（ISO 含 T）的记录不能丢。

    回归点：created_at 形如 2026-06-30T20:15:33，'T' 与拼接 ' 23:59:59'
    的空格比较会错序，故上界用次日零点 < 比较。
    """
    _seed_class(["S01", "S02", "S03"])
    _insert("S01", "B01", "课时作业", "U1", created_at="2026-06-29T20:00:00")
    _insert("S02", "B01", "课时作业", "U1", created_at="2026-06-30T23:30:00")  # 当天最晚
    _insert("S03", "B01", "课时作业", "U1", created_at="2026-07-01T08:00:00")

    def n(**kw):
        return len(db.list_errors(**{k: v for k, v in kw.items() if v}, limit=10))

    assert n(date_from="2026-06-29", date_to="2026-06-30") == 2   # 修复前丢当天晚记录
    assert n(date_from="2026-06-30") == 2
    assert n(date_to="2026-06-30") == 2
    assert n(date_from="2026-07-01") == 1
    assert n() == 3


def test_confirm_errors_bulk_scoped_to_batch(isolated_db):
    """批量确认 batch_no 只作用于该批次的待确认记录（新旧数据都兼容）。"""
    _seed_class(["S01", "S02", "S03"])
    for s in ("S01", "S02"):
        _insert(s, "B01", "课时作业", "Unit 1", confirm=False)
    _insert("S03", "B01", "课时作业", "Unit 2", confirm=False)
    _insert("S01", "A01", "课时作业", None, unit="1单元2课时", confirm=False)

    n = db.confirm_errors_bulk("accept", batch_no="Unit 1")
    assert n == 2
    assert len(db.list_errors(confirmed=False, limit=10)) == 2      # Unit 2 + 旧批次
    assert db.confirm_errors_bulk("accept", batch_no="1单元2课时") == 1  # 旧数据也命中


# ---------------------------------------------------------------- service 层

def test_decision_sheet_batch_and_range_scope(isolated_db):
    """决策单范围参数：批次多选 / 阶段范围各算各的口径，scope_label 人读。"""
    _seed_class(["S%02d" % i for i in range(1, 11)])
    for i in range(5):
        _insert("S%02d" % (i + 1), "B01", "课时作业", "Unit 1",
                created_at="2025-09-26T10:00:00")
    for i in range(3):
        _insert("S%02d" % (i + 1), "B02", "课时作业", "Unit 2",
                created_at="2025-10-17T10:00:00")
    for i in range(4):
        _insert("S%02d" % (i + 1), "C03", "考试试卷", "期中考试",
                created_at="2025-11-07T10:00:00")

    # 批次多选
    s = decision.build_decision_sheet(batch_nos=["Unit 1", "Unit 2"], save=False)
    assert s["total_confirmed"] == 8
    assert s["scope_label"] == "Unit 1、Unit 2"
    assert s["batch_nos"] == ["Unit 1", "Unit 2"]
    assert s["scope"].startswith("batch:Unit 1,Unit 2")

    # 阶段范围（上学期）
    s2 = decision.build_decision_sheet(date_from="2025-09-01",
                                       date_to="2025-10-31", save=False)
    assert s2["total_confirmed"] == 8
    assert s2["scope_label"] == "2025-09-01 ~ 2025-10-31"
    assert s2["date_from"] == "2025-09-01" and s2["date_to"] == "2025-10-31"

    # 全空 = 全部历史
    s3 = decision.build_decision_sheet(save=False)
    assert s3["total_confirmed"] == 12
    assert s3["scope_label"] == "全部历史数据"
    assert s3["scope"] == "task:all"


# ---------------------------------------------------------------- api 层

def test_error_batches_endpoint(isolated_db):
    _seed_class(["S01", "S02"])
    _insert("S01", "B01", "课时作业", "Unit 1", class_name="九(1)班")
    _insert("S02", "C03", "考试试卷", "期中考试", class_name="九(2)班")

    r = client.get("/api/errors/batches")
    assert r.status_code == 200
    bs = {b["batch_no"]: b for b in r.json()["batches"]}
    assert set(bs) == {"Unit 1", "期中考试"}
    assert bs["期中考试"]["exam_type"] == "考试试卷"

    r1 = client.get("/api/errors/batches", params={"class_name": "九(1)班"})
    assert [b["batch_no"] for b in r1.json()["batches"]] == ["Unit 1"]


def test_decision_sheet_endpoint_batch_params(isolated_db):
    """GET /decision-sheet：batch_nos 逗号分隔多选 + 阶段范围。"""
    _seed_class(["S01", "S02", "S03"])
    for i in range(3):
        _insert("S%02d" % (i + 1), "B01", "课时作业", "Unit 1",
                created_at="2025-09-26T10:00:00")
    for i in range(2):
        _insert("S%02d" % (i + 1), "B01", "考试试卷", "期中考试",
                created_at="2025-11-07T10:00:00")

    r = client.get("/api/decision-sheet",
                   params={"batch_nos": "Unit 1, 期中考试"})
    assert r.status_code == 200
    sheet = r.json()
    assert sheet["total_confirmed"] == 5
    assert sheet["scope_label"] == "Unit 1、期中考试"

    r2 = client.get("/api/decision-sheet",
                    params={"date_from": "2025-11-01", "date_to": "2025-11-30"})
    assert r2.json()["total_confirmed"] == 2


def test_dashboard_and_trends_batch_param(isolated_db):
    """错题确认看板 / 错因分析曲线：batch_no 只看某次作业或考试。"""
    _seed_class(["S01", "S02", "S03"])
    _insert("S01", "B01", "课时作业", "Unit 1", created_at="2025-09-26T10:00:00")
    _insert("S02", "B01", "课时作业", "Unit 1", created_at="2025-09-26T11:00:00")
    _insert("S03", "B01", "考试试卷", "期中考试", created_at="2025-11-07T10:00:00")

    d = client.get("/api/dashboard", params={"batch_no": "Unit 1"}).json()
    assert d["total_errors"] == 2 and d["confirmed"] == 2
    assert client.get("/api/dashboard").json()["total_errors"] == 3

    t = client.get("/api/trends/categories", params={"batch_no": "Unit 1"}).json()
    points = [p for s in t["series"] for p in s["points"]]
    assert sum(p["count"] for p in points) == 2          # 只含 Unit 1 的两人次


def test_confirm_bulk_endpoint_scoped_to_batch(isolated_db):
    """POST /errors/confirm-bulk：带 batch_no 时只批量确认该批次。"""
    _seed_class(["S01", "S02"])
    _insert("S01", "B01", "课时作业", "Unit 1", confirm=False)
    _insert("S02", "B01", "课时作业", "Unit 2", confirm=False)

    r = client.post("/api/errors/confirm-bulk", json={
        "action": "accept", "min_confidence": 0.8, "batch_no": "Unit 1"})
    assert r.status_code == 200 and r.json()["confirmed"] == 1
    d = client.get("/api/dashboard").json()
    assert d["pending_review"] == 1                       # Unit 2 仍待确认


def test_decision_sheet_export_with_scope(isolated_db):
    """导出与页面同源：批次范围参数正常渲染（打印友好 HTML）。"""
    _seed_class(["S01", "S02"])
    _insert("S01", "B01", "课时作业", "Unit 1")
    _insert("S02", "C03", "考试试卷", "期中考试")

    r = client.get("/api/decision-sheet/export",
                   params={"batch_nos": "Unit 1", "fmt": "html"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "讲评课方案" in r.text


# ---------------------------------------------------------------- 提示词回归

def test_prompts_distinguish_printed_vs_handwritten():
    """三处解析提示词都必须写明：印刷已给属题干，学生补写才是作答。

    填空题印刷部分可能已给字母前缀（incom___）或括号提示词 ((great))，
    学生只补写几个字母/单词——不区分会把题干当错因证据。
    """
    from app.providers import agent_chat
    from app.services import photo_check

    for text in (agent_chat._EXTRACT_RULES,
                 photo_check._PARSE_PROMPT_TEMPLATE):
        assert "印刷已给" in text and "补写" in text
    assert "补写" in photo_check._MD_PARSE_TEMPLATE      # DeepSeek 结构化路同样约束
    # 典型例子必须出现（帮助模型对齐补全单词场景）
    assert "incom___" in agent_chat._EXTRACT_RULES
