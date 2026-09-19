"""学生历史数据清理：按时间维度删除错题与照片登记（一周前 / 一个月前 / 全部）。"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import db as appdb
from app.main import app

client = TestClient(app)


def _seed(code: str = "S01", n_old: int = 3, n_new: int = 2, other: str = "S02") -> None:
    """给 code 建 n_old 条 10 天前的记录 + n_new 条刚产生的记录；other 建 1 条旧记录。

    照片登记同理：旧 1 条 + 新 1 条（code 名下）。
    """
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    new = datetime.now().isoformat(timespec="seconds")
    client.post("/api/students", json={"student_code": code})
    client.post("/api/students", json={"student_code": other})
    with appdb.get_conn() as conn:
        for i in range(n_old):
            conn.execute(
                "INSERT INTO errors(student_code, class_name, question, qtype, "
                "category_id, created_at) VALUES(?,?,?,?,?,?)",
                (code, "", "Q old %d" % i, "单选", "B01", old))
        for i in range(n_new):
            conn.execute(
                "INSERT INTO errors(student_code, class_name, question, qtype, "
                "category_id, created_at) VALUES(?,?,?,?,?,?)",
                (code, "", "Q new %d" % i, "单选", "B01", new))
        conn.execute(
            "INSERT INTO errors(student_code, class_name, question, qtype, "
            "category_id, created_at) VALUES(?,?,?,?,?,?)",
            (other, "", "Q other", "单选", "B01", old))
        conn.execute(
            "INSERT INTO paper_files(student_code, student_name, file_name, created_at) "
            "VALUES(?,?,?,?)", (code, "王一", "S01/1.jpg", old))
        conn.execute(
            "INSERT INTO paper_files(student_code, student_name, file_name, created_at) "
            "VALUES(?,?,?,?)", (code, "王一", "S01/2.jpg", new))


def test_cleanup_week_cutoff_db():
    """一周前维度：只删早于截止时间的记录，新纪录与其他学生不受影响。"""
    _seed()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    removed = appdb.cleanup_student_history("S01", cutoff)
    assert removed == {"errors": 3, "paper_files": 1}
    with appdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT question FROM errors WHERE student_code='S01' ORDER BY question"
        ).fetchall()
        assert [r["question"] for r in rows] == ["Q new 0", "Q new 1"]
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S02'").fetchone()["c"] == 1
        pf = conn.execute(
            "SELECT file_name FROM paper_files WHERE student_code='S01'").fetchall()
        assert [r["file_name"] for r in pf] == ["S01/2.jpg"]


def test_cleanup_all_db():
    """全部清除：该生错题与照片登记清零，其他学生不受影响。"""
    _seed()
    removed = appdb.cleanup_student_history("S01", None)
    assert removed == {"errors": 5, "paper_files": 2}
    with appdb.get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S01'").fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM paper_files WHERE student_code='S01'"
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S02'").fetchone()["c"] == 1


def test_cleanup_api_dimensions():
    """API 维度映射：1w 按截止时间删除，all 全部清除；档案随之更新。"""
    _seed()
    r = client.post("/api/students/S01/cleanup", json={"dimension": "1w"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["label"] == "一周前"
    assert body["removed"] == {"errors": 3, "paper_files": 1}
    p = client.get("/api/students/S01/profile").json()
    assert len(p["recent"]) == 2   # 只剩 2 条新纪录

    r = client.post("/api/students/S01/cleanup", json={"dimension": "all"})
    assert r.status_code == 200
    assert r.json()["label"] == "全部"
    assert r.json()["removed"] == {"errors": 2, "paper_files": 1}
    p = client.get("/api/students/S01/profile").json()
    assert p["recent"] == [] and p["total_confirmed"] == 0


def test_cleanup_api_month_dimension():
    """1m 维度（30 天截止）：10 天前的记录不早于截止线，不删。"""
    _seed()
    r = client.post("/api/students/S01/cleanup", json={"dimension": "1m"})
    assert r.status_code == 200
    assert r.json()["removed"] == {"errors": 0, "paper_files": 0}


def test_cleanup_by_category():
    """按错因类别：只删指定类别的错题（不限时间），照片登记不删，其他类别保留。"""
    _seed()   # S01：5 条 B01/单选错题 + 2 条照片登记
    old = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    with appdb.get_conn() as conn:
        for i in range(2):
            conn.execute(
                "INSERT INTO errors(student_code, class_name, question, qtype, "
                "category_id, created_at) VALUES(?,?,?,?,?,?)",
                ("S01", "", "Q A03 %d" % i, "单选", "A03", old))
    r = client.post("/api/students/S01/cleanup",
                    json={"dimension": "category", "category_id": "A03"})
    assert r.status_code == 200
    assert r.json()["label"] == "错因类别 A03"
    assert r.json()["removed"] == {"errors": 2, "paper_files": 0}
    with appdb.get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S01' "
            "AND category_id='A03'").fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S01'").fetchone()["c"] == 5
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM paper_files WHERE student_code='S01'").fetchone()["c"] == 2


def test_cleanup_by_qtype():
    """按题型：只删指定题型的错题，其他题型保留。"""
    _seed()
    new = datetime.now().isoformat(timespec="seconds")
    with appdb.get_conn() as conn:
        conn.execute(
            "INSERT INTO errors(student_code, class_name, question, qtype, "
            "category_id, created_at) VALUES(?,?,?,?,?,?)",
            ("S01", "", "Q 完形", "完形填空", "C01", new))
    r = client.post("/api/students/S01/cleanup",
                    json={"dimension": "qtype", "qtype": "完形填空"})
    assert r.status_code == 200
    assert r.json()["label"] == "题型 完形填空"
    assert r.json()["removed"] == {"errors": 1, "paper_files": 0}
    with appdb.get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S01'").fetchone()["c"] == 5
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code='S01' "
            "AND qtype='完形填空'").fetchone()["c"] == 0


def test_cleanup_api_missing_params():
    """category/qtype 维度缺少对应参数 → 400。"""
    client.post("/api/students", json={"student_code": "P01"})
    r = client.post("/api/students/P01/cleanup", json={"dimension": "category"})
    assert r.status_code == 400
    r = client.post("/api/students/P01/cleanup", json={"dimension": "qtype"})
    assert r.status_code == 400


def test_cleanup_api_validation():
    """不存在学生 404；非法维度 400。"""
    r = client.post("/api/students/NOPE/cleanup", json={"dimension": "1w"})
    assert r.status_code == 404
    client.post("/api/students", json={"student_code": "V01"})
    r = client.post("/api/students/V01/cleanup", json={"dimension": "1y"})
    assert r.status_code == 400
