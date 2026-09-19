"""API 端到端：录入→归因→确认→练习→决策单→曲线→修正率→CSV 导出。"""
import base64

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_ontology():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "eta-assistant"
    assert "mock" not in body  # 真实 AI 单模式：无 mock 字段
    assert body["ontology_categories"] == 36

    r = client.get("/api/ontology")
    assert r.status_code == 200
    assert len(r.json()["categories"]) == 36


def test_full_workflow_end_to_end():
    # 1. 学生名单导入（教师本机视图含姓名；无任何评价/排名字段）
    r = client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "张三"}, {"code": "S02", "name": "李四"}]})
    assert r.status_code == 200
    assert r.json()["imported"] == 2
    roster = client.get("/api/students").json()
    assert all("name_local" in s for s in roster)
    assert all("score" not in s and "rank" not in s for s in roster)

    # 2. 录入归因（LLM 桩命中 B01 时态）
    r = client.post("/api/errors/scan", json={
        "question": "Choose the correct answer.",
        "answer": "I have seen the film yesterday.",
        "correct": "I saw the film yesterday.",
        "qtype": "单选", "exam_type": "课时作业", "batch_no": "Unit 4",
        "student_code": "S01"})
    assert r.status_code == 200
    eid = r.json()["error_id"]
    assert r.json()["ai"]["category_id"] == "B01"

    # 3. 待确认列表 → 教师改判为 A03（override 优先）
    pending = client.get("/api/errors", params={"confirmed": False}).json()
    assert any(e["id"] == eid for e in pending)
    r = client.post("/api/errors/%d/confirm" % eid,
                    json={"action": "modify", "override": "A03"})
    assert r.status_code == 200
    assert r.json()["error"]["final_category"] == "A03"

    # 4. 再录一条（阅读推断题命中 C03）并采纳
    r = client.post("/api/errors/scan", json={
        "question": "What can we learn from the passage?",
        "answer": "The writer wants us to stop using phones.",
        "qtype": "阅读理解", "exam_type": "考试试卷", "batch_no": "期中考试",
        "student_code": "S02"})
    eid2 = r.json()["error_id"]
    assert r.json()["ai"]["category_id"] == "C03"
    client.post("/api/errors/%d/confirm" % eid2, json={"action": "accept"})

    # 5. 补足决策单需要的量：B01 类 12 条（不同学生，采纳）
    for i in range(3, 15):
        client.post("/api/errors/scan", json={
            "question": "Choose the correct answer.",
            "answer": "I have watched the movie two days ago.",
            "correct": "watched", "qtype": "单选", "exam_type": "课时作业",
            "batch_no": "Unit 4", "student_code": "S%02d" % i})
        er = client.get("/api/errors", params={"confirmed": False}).json()
        last = er[0]
        client.post("/api/errors/%d/confirm" % last["id"], json={"action": "accept"})

    # 6. 补偿练习生成 + 审校
    r = client.post("/api/practice/generate", json={"category_id": "B01", "n": 3})
    assert r.status_code == 200
    pid = r.json()["practice_id"]
    assert r.json()["items"][0]["ai_tag"] == "AI 生成·待审校"
    assert client.post("/api/practice/%d/approve" % pid).status_code == 200

    # 7. 决策单（任务类型维度：课时作业）
    r = client.get("/api/decision-sheet", params={"exam_type": "课时作业"})
    sheet = r.json()
    assert r.status_code == 200
    focus_ids = [f["category_id"] for f in sheet["focus"]]
    assert "B01" in focus_ids
    assert sheet["focus"][0]["approved_practice_count"] >= 1
    assert "不构成学生评价" in sheet["notice"]

    # 8. 演变曲线（按录入时间分周）+ 教师修正率（曲线响应内嵌 overrides，前端趋势段同源）
    r = client.get("/api/trends/categories", params={"by": "week"})
    trends = r.json()
    assert len(trends["x_labels"]) == 1 and "周" in trends["x_labels"][0]
    cats = {s["category_id"] for s in trends["series"]}
    assert {"B01", "C03"} <= cats

    stats = trends["overrides"]
    assert stats["modified"] == 1  # 1 次改判
    assert stats["override_rate"] > 0

    # 10. CSV 导出（BOM + 无姓名；v3 表头含 exam_type/batch_no 列）
    r = client.get("/api/export/errors.csv")
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert text.startswith("id,class_name,exam_type,batch_no,student_code")
    assert "张三" not in text and "S01" in text
    assert "A03" in text


def test_scan_with_image_ocr(monkeypatch):
    """OCR 通道桩注入：识别结果（题型/原文拆分）透传到归因与响应。"""
    from app.providers import ocr as ocr_mod

    def fake_ocr(image_base64):
        return {"question": "He ____ (go) to the park yesterday.",
                "qtype": "单选", "passage": "",
                "note": "桩识别（测试） ｜ AI 生成"}

    monkeypatch.setattr(ocr_mod, "ocr_image", fake_ocr)
    b64 = base64.b64encode(b"fake-image-bytes").decode()
    r = client.post("/api/errors/scan", json={
        "answer": "has gone", "qtype": "", "image_base64": b64})
    assert r.status_code == 200
    assert r.json()["ocr_note"] and "AI 生成" in r.json()["ocr_note"]
    assert r.json()["qtype"] == "单选"  # OCR 识别的题型透传
    assert r.json()["ai"]["category_id"] == "B01"  # 跨字段：题干 yesterday + 作答 has gone


def test_scan_requires_question():
    r = client.post("/api/errors/scan", json={"answer": "x"})
    assert r.status_code == 400


def test_confirm_validates_override_category():
    r = client.post("/api/errors/scan", json={"question": "q", "answer": "ok then"})
    eid = r.json()["error_id"]
    r = client.post("/api/errors/%d/confirm" % eid,
                    json={"action": "modify", "override": "Z99"})
    assert r.status_code == 400


def test_no_student_evaluation_endpoints():
    """反标签化：路由层不存在按学生输出评价/报告/排名的端点。"""
    # 新版 FastAPI 的 app.routes 含 _IncludedRouter 等无 path 属性的项，跳过
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for path in paths:
        assert "report-card" not in path
        assert "student-eval" not in path
        assert "rank" not in path


# ---------------------------------------------------------------- 批量录入

def test_scan_batch_multi_student_and_dedup():
    """批量录入：一题多生展开、同题只归因一次。"""
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "张三"}, {"code": "S02", "name": "李四"}]})
    text = "\n".join([
        "S01,S02 | I have seen the film yesterday. | have seen | saw",   # 两人一题
        "S02 | There ____ a book on the desk. | There have a book | is",  # 单人（B06）
    ])
    r = client.post("/api/errors/scan-batch", json={"text": text})
    assert r.status_code == 200
    body = r.json()["stats"]
    assert body["lines"] == 2
    assert body["inserted"] == 3          # 2 + 1
    assert body["llm_calls"] == 2         # 两题各异
    assert body["failed"] == 0
    ok = [x for x in r.json()["results"] if x["ok"]]
    assert ok[0]["students"] == 2 and ok[0]["category_id"] == "B01"
    errs = client.get("/api/errors?student_code=S01").json()
    assert len(errs) == 1


def test_scan_batch_same_question_single_llm_call():
    """同题同答分两行（不同学生）：只调一次大模型，展开成两条。"""
    client.post("/api/students/import", json={"rows": [{"code": "S01"}, {"code": "S02"}]})
    text = ("S01 | I have seen the film yesterday. | have seen\n"
            "S02 | I have seen the film yesterday. | have seen")
    body = client.post("/api/errors/scan-batch", json={"text": text}).json()["stats"]
    assert body["inserted"] == 2
    assert body["llm_calls"] == 1


def test_scan_batch_bad_lines_reported_not_fatal():
    """坏学号/格式错误的行逐行报错，不阻断其他行。"""
    client.post("/api/students/import", json={"rows": [{"code": "S01"}]})
    text = "\n".join([
        "S01 | There ____ a book on the desk. | There have a book",  # 合法
        "9999 | I have seen the film yesterday. | have seen",        # 学号不在名册
        "只有一段",                                                 # 段数不足
    ])
    body = client.post("/api/errors/scan-batch", json={"text": text}).json()
    assert body["stats"]["failed"] == 2
    assert body["stats"]["inserted"] == 1
    msgs = [x["msg"] for x in body["results"] if not x["ok"]]
    assert any("名册" in m for m in msgs)
    assert any("格式" in m for m in msgs)


# ---------------------------------------------------------------- 拍照切题（多图 → AI 切题 → 提交）

def test_photo_split_and_submit(monkeypatch):
    """多图拍照切题：一页多题/多题型自动切分，教师核对后提交，多学生展开。"""
    from app.providers import ocr as ocr_mod

    def fake_ocr_page(image_base64):
        # 两页各含部分题（跨页合并由切题 LLM 桩负责，这里只验证透传）
        return "1. He ____ to Beijing twice last year.\n2. 判断题…"

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr_page)
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "张三"}, {"code": "S02", "name": "李四"}]})

    # 第一步：切题预览（不入库）
    b64 = base64.b64encode(b"fake-page").decode()
    r = client.post("/api/errors/scan-photos", json={"images_base64": [b64, b64]})
    assert r.status_code == 200
    body = r.json()
    assert body["pages"] == 2
    assert len(body["questions"]) == 4              # 桩切出 4 题（一页多题）
    qtypes = {q["qtype"] for q in body["questions"]}
    assert {"单选", "判断题", "阅读理解"} <= qtypes   # 混合题型
    assert "其他" in qtypes                          # 未知题型规范化为“其他”
    assert any(q["passage"] for q in body["questions"])  # 阅读原文随题带出
    assert client.get("/api/errors").json() == []   # 预览阶段不入库

    # 第二步：提交勾选题（第 1 题两人共错；第 2 题不关联学生；名册外代号报错不阻断）
    r = client.post("/api/errors/scan-photos-submit", json={
        "questions": [
            {"qtype": "单选", "question": "He ____ to Beijing twice last year.",
             "answer": "has been", "students": ["S01", "S02"]},
            {"qtype": "判断题", "question": "There is a book on the desk. ( )"},
            {"qtype": "单选", "question": "Bad question", "students": ["9999"]},
        ], "exam_type": "考试试卷", "batch_no": "Unit 5"})
    assert r.status_code == 200
    stats = r.json()["stats"]
    assert stats["inserted"] == 3      # 2（两人）+ 1（班级级）
    assert stats["failed"] == 1        # 名册外代号
    ok = [x for x in r.json()["results"] if x["ok"]]
    assert ok[0]["students"] == 2 and ok[0]["category_id"] == "B01"
    errs = client.get("/api/errors?confirmed=false").json()
    assert len(errs) == 3


def test_photo_split_validation(monkeypatch):
    """空图/超限的入参校验；OCR 空文本报可读错误。"""
    from app.providers import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "ocr_page", lambda b64: "  ")
    assert client.post("/api/errors/scan-photos",
                       json={"images_base64": []}).status_code == 400
    b64 = base64.b64encode(b"x").decode()
    assert client.post("/api/errors/scan-photos",
                       json={"images_base64": [b64] * 11}).status_code == 400
    r = client.post("/api/errors/scan-photos", json={"images_base64": [b64]})
    assert r.status_code == 400 and "未识别到文字" in r.json()["detail"]


# ---------------------------------------------------------------- 归因复核批量采纳（减负）

def test_confirm_bulk_high_confidence_only():
    """批量采纳仅作用于高置信度待复核记录，低置信度留待逐条复核。"""
    # 高置信度（桩 B01=0.9）与低置信度（未识别=0.3）各一条
    client.post("/api/errors/scan", json={
        "question": "I have seen the film yesterday.", "answer": "have seen"})
    client.post("/api/errors/scan", json={
        "question": "no signal question", "answer": "nothing to match"})
    pending = client.get("/api/errors?confirmed=false").json()
    assert len(pending) == 2

    r = client.post("/api/errors/confirm-bulk",
                    json={"action": "accept", "min_confidence": 0.8})
    assert r.status_code == 200 and r.json()["confirmed"] == 1
    # 剩余低置信度仍待复核
    pending = client.get("/api/errors?confirmed=false").json()
    assert len(pending) == 1 and pending[0]["confidence"] < 0.8

    # 采纳全部 → 待复核清零
    r = client.post("/api/errors/confirm-bulk", json={"action": "accept"})
    assert r.json()["confirmed"] == 1
    assert client.get("/api/errors?confirmed=false").json() == []
    # 再批量采纳 → 无待复核记录报 400
    assert client.post("/api/errors/confirm-bulk",
                       json={"action": "accept"}).status_code == 400


# ---------------------------------------------------------------- 学生管理（增删改）

def test_student_crud_flow():
    # 新增
    r = client.post("/api/students", json={
        "student_code": "9101", "name_local": "崔梦琪", "class_name": "九(1)班"})
    assert r.status_code == 200
    # 重复代号 → 400
    assert client.post("/api/students", json={"student_code": "9101"}).status_code == 400
    # 录一条错题关联 9101（验证改代号级联）
    scan = client.post("/api/errors/scan", json={
        "question": "I have seen the film yesterday.", "answer": "have seen",
        "student_code": "9101", "class_name": "九(1)班"}).json()
    # 改代号 + 姓名
    r = client.put("/api/students/9101", json={"new_code": "9201", "name_local": "崔小琪"})
    assert r.status_code == 200
    assert len(client.get("/api/errors?student_code=9201").json()) == 1  # 关联跟随
    # 改成已占用代号 → 400
    client.post("/api/students", json={"student_code": "9301"})
    assert client.put("/api/students/9201", json={"new_code": "9301"}).status_code == 400
    # 删除：默认连同该生全部错题删除（purge）
    r = client.delete("/api/students/9201")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "purge" and body["removed_errors"] == 1
    assert client.get("/api/errors?student_code=9201").json() == []
    assert client.delete("/api/students/9201").status_code == 404

    # 保留模式：purge_errors=false 时错题转为班级级记录
    client.post("/api/students", json={"student_code": "9102"})
    s = client.post("/api/errors/scan", json={
        "question": "I have seen the film yesterday.", "answer": "have seen",
        "student_code": "9102"}).json()
    r = client.delete("/api/students/9102?purge_errors=false")
    assert r.json()["mode"] == "keep" and r.json()["removed_errors"] == 1
    kept = client.get("/api/errors").json()
    assert any(e["id"] == s["error_id"] and e["student_code"] == "" for e in kept)


# ---------------------------------------------------------------- 班级管理（动态初始化）

def test_class_crud_flow():
    # 新建班级：可先建班，再录学生/错题
    r = client.post("/api/classes", json={"name": "九(1)班"})
    assert r.status_code == 200
    assert "九(1)班" in client.get("/api/classes").json()["classes"]
    # 重复新建 → 400
    assert client.post("/api/classes", json={"name": "九(1)班"}).status_code == 400
    assert client.post("/api/classes", json={"name": "  "}).status_code == 400
    # 建班后录学生 + 错题，验证改名级联
    client.post("/api/students", json={"student_code": "9101", "name_local": "崔梦琪",
                                       "class_name": "九(1)班"})
    s = client.post("/api/errors/scan", json={
        "question": "I have seen the film yesterday.", "answer": "have seen",
        "class_name": "九(1)班"}).json()
    r = client.put("/api/classes/九(1)班", json={"new_name": "九(2)班"})
    assert r.status_code == 200
    assert "九(2)班" in client.get("/api/classes").json()["classes"]
    # 学生与错题的班级归属同步更新
    assert client.get("/api/students").json()[0]["class_name"] == "九(2)班"
    assert client.get("/api/errors").json()[0]["class_name"] == "九(2)班"
    # 有数据的班级删除：级联删除全部数据（学生/错题一并删除）
    r = client.delete("/api/classes/九(2)班")
    assert r.status_code == 200
    assert r.json()["removed_students"] == 1 and r.json()["removed_errors"] == 1
    assert client.get("/api/students").json() == []
    assert client.get("/api/errors").json() == []
    # 改名为已存在的班级 → 400
    client.post("/api/classes", json={"name": "九(3)班"})
    client.post("/api/classes", json={"name": "九(1)班"})
    client.post("/api/students", json={"student_code": "9102", "class_name": "九(3)班"})
    assert client.put("/api/classes/九(3)班", json={"new_name": "九(1)班"}).status_code == 400
    # 空班级可删除
    assert client.delete("/api/classes/九(3)班").status_code == 200
    assert client.delete("/api/classes/九(1)班").status_code == 200
    assert client.get("/api/classes").json()["classes"] == []


def test_delete_class_with_legacy_revisions_table():
    """旧库遗留 revisions 表（含指向 errors 的外键）时，删除班级/学生不得报外键错误。"""
    import sqlite3

    from app import db as appdb
    db_path = appdb.config.get_db_path()
    conn = sqlite3.connect(db_path)
    # 模拟二改功能遗留表：error_id 外键指向 errors.id（无级联删除）
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_id INTEGER NOT NULL REFERENCES errors(id),
            round INTEGER NOT NULL DEFAULT 1,
            content TEXT
        );
    """)
    client.post("/api/classes", json={"name": "旧库班"})
    client.post("/api/students", json={"student_code": "9101", "class_name": "旧库班"})
    eid = client.post("/api/errors/scan", json={
        "question": "I have seen the film yesterday.", "answer": "have seen",
        "class_name": "旧库班", "student_code": "9101"}).json()["error_id"]
    conn.execute("INSERT INTO revisions(error_id, round, content) VALUES(?, 1, '二改')",
                 (eid,))
    conn.commit()
    conn.close()
    # 级联删除班级：先清 revisions 引用再删错题，不得 IntegrityError
    r = client.delete("/api/classes/旧库班")
    assert r.status_code == 200
    assert r.json()["removed_students"] == 1 and r.json()["removed_errors"] == 1


# ---------------------------------------------------------------- 决策单 AI 讲评策略

def test_decision_ai_strategy(monkeypatch):
    """AI 讲评策略：基于规则决策单数据生成，返回策略与合规标注。"""
    import json as _json
    from app.providers import llm as llm_mod

    def fake_chat(messages, **kwargs):
        return _json.dumps({
            "overview": "本班时态误用是核心问题，建议以时间轴对比切入。",
            "timing": "15 分钟重点讲 B01，10 分钟变式训练，其余个别反馈。",
            "focus_advice": [{"category_id": "B01", "hook": "从错例 I have seen… yesterday 切入",
                              "steps": "先画时间轴再对比完成体与过去时", "board": "时间轴图"}],
            "skip_advice": "课后个别批改反馈",
            "tutor_advice": "课后 10 分钟面批",
            "risk": "高频错因需重点讲评"}, ensure_ascii=False)

    monkeypatch.setattr(llm_mod, "chat", fake_chat)
    for i in range(4):  # 4 条同类别确认错题 → 达到重点讲阈值（≥3 人次）
        s = client.post("/api/errors/scan", json={
            "question": "Q%d I have seen the film yesterday." % i,
            "answer": "have seen"}).json()
        client.post("/api/errors/%d/confirm" % s["error_id"], json={"action": "accept"})
    r = client.post("/api/decision-sheet/ai", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"]["overview"]
    assert body["strategy"]["focus_advice"][0]["category_id"] == "B01"
    assert body["ai_tag"]
    assert body["based_on"]["focus_count"] >= 1


def test_decision_ai_requires_data():
    """空库无确认错因时显式报错（规则决策单不受影响）。"""
    r = client.post("/api/decision-sheet/ai", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------- 练习题量 1–100

def test_practice_n_range_validation():
    assert client.post("/api/practice/generate",
                       json={"category_id": "A01", "n": 101}).status_code == 422
    assert client.post("/api/practice/generate",
                       json={"category_id": "A01", "n": 0}).status_code == 422
    r = client.post("/api/practice/generate", json={"category_id": "A01", "n": 5})
    assert r.status_code == 200
    assert len(r.json()["items"]) <= 5


# ---------------------------------------------------------------- 多类一次生成

def test_practice_multi_category_generate():
    """多类一次生成：去重、每类各成一套；单类平铺兼容旧返回；超限/空选显式报错。"""
    r = client.post("/api/practice/generate",
                    json={"category_ids": ["A01", "C03", "A01"], "n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2                       # 重复类别只算一次
    assert len({g["practice_id"] for g in body["generated"]}) == 2   # 每类各成一套
    assert body["total_items"] == sum(len(g["items"]) for g in body["generated"])

    # 单类：返回结构兼容旧客户端（平铺 practice_id / items）
    r = client.post("/api/practice/generate", json={"category_ids": ["B01"], "n": 2})
    assert r.status_code == 200
    assert r.json()["practice_id"] and r.json()["items"]

    # 超上限（7 类）与未选类别 → 400
    assert client.post("/api/practice/generate",
                       json={"category_ids": ["A%02d" % i for i in range(1, 8)],
                             "n": 2}).status_code == 400
    assert client.post("/api/practice/generate", json={"n": 2}).status_code == 400


# ---------------------------------------------------------------- AI 组卷

def _confirm_errors(n=3, class_name=""):
    """造 n 条已确认错题（组卷/学生 CSV 测试的数据底座，桩归因命中 B01）。"""
    for i in range(n):
        s = client.post("/api/errors/scan", json={
            "question": "Q%d I have seen the film yesterday." % i,
            "answer": "have seen", "unit": "Unit 1",
            "class_name": class_name}).json()
        client.post("/api/errors/%d/confirm" % s["error_id"], json={"action": "accept"})


def test_paper_generate_requires_confirmed_errors():
    """空库无确认错题时组卷显式报错。"""
    assert client.post("/api/paper/generate", json={"n": 5}).status_code == 400


def test_paper_generate_approve_and_list():
    """组卷：draft → 列表 → 审校 → 已审校；重复审校 404；条件参数枚举校验。"""
    _confirm_errors(2)
    r = client.post("/api/paper/generate", json={"n": 10})
    assert r.status_code == 200
    body = r.json()
    pid = body["paper_id"]
    assert body["title"] and body["items"]
    assert all(it["ai_tag"] == "AI 生成·待审校" for it in body["items"])

    # 条件组卷：单元范围 + 难度 + 题型偏好（非法枚举 422）
    r = client.post("/api/paper/generate", json={
        "n": 5, "unit": "Unit 1", "difficulty": "easy", "qtype_pref": "mc"})
    assert r.status_code == 200
    assert client.post("/api/paper/generate",
                       json={"n": 5, "difficulty": "impossible"}).status_code == 422
    assert client.post("/api/paper/generate",
                       json={"n": 5, "qtype_pref": "oral"}).status_code == 422
    # 前端未选下拉时传空字符串 → 视同未选（不得 422）
    r = client.post("/api/paper/generate", json={
        "n": 3, "title": "", "unit": "", "exam_type": "", "class_name": "",
        "difficulty": "", "qtype_pref": ""})
    assert r.status_code == 200

    assert any(p["id"] == pid for p in
               client.get("/api/papers", params={"status": "draft"}).json())
    assert client.post("/api/paper/%d/approve" % pid).status_code == 200
    assert client.post("/api/paper/%d/approve" % pid).status_code == 404
    assert any(p["id"] == pid for p in
               client.get("/api/papers", params={"status": "approved"}).json())


def test_generate_with_date_scope_params():
    """时间范围参数（v4.12）：练习注入真实错例 + scope 留档；组卷只统计阶段内错题；
    范围内无错题时组卷显式 400（练习退回通用示例照常生成）；空串视同未选。"""
    _confirm_errors(2)   # 桩归因命中 B01，created_at 为当前时间

    # 练习：范围内两条错例都注入，时间标签随练习留档
    r = client.post("/api/practice/generate",
                    json={"category_id": "B01", "n": 2,
                          "date_from": "2020-01-01", "date_to": ""})
    assert r.status_code == 200
    assert r.json()["scope"] == "2020-01-01 ~ 今"
    assert r.json()["real_example_n"] == 2
    # 范围外（未来）：无真实错例也能生成（退回本体库通用示例）
    r2 = client.post("/api/practice/generate",
                     json={"category_id": "B01", "n": 2, "date_from": "2099-01-01"})
    assert r2.status_code == 200 and r2.json()["real_example_n"] == 0

    # 组卷：只统计阶段内确认错题（scope 含时间标签）；未来阶段无错题 → 400
    r3 = client.post("/api/paper/generate",
                     json={"n": 3, "date_from": "2020-01-01", "date_to": ""})
    assert r3.status_code == 200
    assert "2020-01-01 ~ 今" in r3.json()["scope"]
    assert client.post("/api/paper/generate",
                       json={"n": 3, "date_from": "2099-01-01"}).status_code == 400

    # 前端清空时间时传空字符串 → 视同未选（不得 422/400）
    assert client.post("/api/practice/generate",
                       json={"category_id": "B01", "n": 2,
                             "date_from": "", "date_to": ""}).status_code == 200
    assert client.post("/api/paper/generate",
                       json={"n": 3, "date_from": "", "date_to": ""}).status_code == 200


# ---------------------------------------------------------------- 导出（练习/试卷/学生）

def test_export_practices_html_and_docx():
    """练习导出：draft 学生卷拒、教师版可；审校后学生卷可；html/docx 双格式。"""
    pid = client.post("/api/practice/generate",
                      json={"category_id": "A01", "n": 2}).json()["practice_id"]

    # 未审校：学生卷不下发（审校环节不可绕过），教师版可光打印核对
    assert client.get("/api/practices/export",
                      params={"ids": pid, "fmt": "html", "answers": 0}).status_code == 400
    r = client.get("/api/practices/export",
                   params={"ids": pid, "fmt": "html", "answers": 1})
    assert r.status_code == 200
    assert "教师版" in r.text and "【答案】" in r.text

    client.post("/api/practice/%d/approve" % pid)
    r = client.get("/api/practices/export",
                   params={"ids": pid, "fmt": "html", "answers": 0})
    assert r.status_code == 200
    assert "学生卷" in r.text and "【答案】" not in r.text
    assert "答：____" in r.text          # 无选项题留作答横线

    r = client.get("/api/practices/export",
                   params={"ids": pid, "fmt": "docx", "answers": 1})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert r.content[:2] == b"PK"        # docx 即 zip 包

    # 多套合并导出 + 非法 ids 解析
    pid2 = client.post("/api/practice/generate",
                       json={"category_id": "B01", "n": 2}).json()["practice_id"]
    client.post("/api/practice/%d/approve" % pid2)
    assert client.get("/api/practices/export",
                      params={"ids": "%d,%d" % (pid, pid2),
                              "fmt": "html", "answers": 0}).status_code == 200
    assert client.get("/api/practices/export",
                      params={"ids": "x"}).status_code == 400


def test_export_paper():
    """试卷导出：未审校学生卷拒；审校后 html/docx 均可；不存在 404。"""
    _confirm_errors(2)
    pid = client.post("/api/paper/generate",
                      json={"n": 5, "title": "期中补偿卷"}).json()["paper_id"]
    assert client.get("/api/papers/%d/export" % pid,
                      params={"fmt": "html", "answers": 0}).status_code == 400
    client.post("/api/paper/%d/approve" % pid)

    r = client.get("/api/papers/%d/export" % pid, params={"fmt": "html", "answers": 1})
    assert r.status_code == 200
    assert "期中补偿卷" in r.text and "【答案】" in r.text
    r = client.get("/api/papers/%d/export" % pid, params={"fmt": "docx", "answers": 0})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.get("/api/papers/999/export").status_code == 404


def test_export_decision_sheet():
    """讲评方案导出：html 打印页三段齐全 + 合规提示语；docx 可下载。"""
    client.post("/api/classes", json={"name": "九(1)班"})
    client.post("/api/students", json={"student_code": "9101",
                                       "class_name": "九(1)班"})
    for i in range(5):
        s = client.post("/api/errors/scan", json={
            "question": "Q%d I have seen the film yesterday." % i,
            "answer": "have seen", "student_code": "9101",
            "class_name": "九(1)班", "exam_type": "课时作业", "batch_no": "Unit 1"}).json()
        client.post("/api/errors/%d/confirm" % s["error_id"], json={"action": "accept"})

    r = client.get("/api/decision-sheet/export",
                   params={"class_name": "九(1)班", "fmt": "html"})
    assert r.status_code == 200
    assert "英语讲评课方案" in r.text
    assert "一、本课重点讲" in r.text and "二、本课不讲清单" in r.text \
        and "三、个别辅导名单" in r.text
    assert "不构成学生评价" in r.text          # 合规提示语随导出保留

    r = client.get("/api/decision-sheet/export",
                   params={"class_name": "九(1)班", "fmt": "docx"})
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_export_students_csv():
    """学生综合信息 CSV：BOM + 列头 + TOP 错因 + 批改均分；无任何排名字段。"""
    client.post("/api/students/import", json={"rows": [
        {"code": "S01", "name": "张三", "class": "九(1)班"},
        {"code": "S02", "name": "李四", "class": "九(1)班"}]})
    for i in range(2):
        s = client.post("/api/errors/scan", json={
            "question": "Q%d I have seen the film yesterday." % i,
            "answer": "have seen", "student_code": "S01"}).json()
        client.post("/api/errors/%d/confirm" % s["error_id"], json={"action": "accept"})

    from app import db as appdb
    appdb.insert_check_session("assignment", "A1", "九(1)班", None, 1, "课时作业",
                               {"results": [{"student_code": "S01", "score": 85.0},
                                            {"student_code": "S02", "score": 90.0}]})

    r = client.get("/api/students/export.csv", params={"class_name": "九(1)班"})
    assert r.status_code == 200
    assert r.text.startswith("\ufeff")           # BOM：Excel 双击直开不乱码

    import csv as _csv
    import io as _io
    rows = list(_csv.reader(_io.StringIO(r.text.lstrip("\ufeff"))))
    data = {row[0]: row for row in rows[1:]}
    assert rows[0][0] == "student_code" and "主要错因" in rows[0][5]
    assert data["S01"][1] == "张三"
    assert data["S01"][3] == "2"                 # 已确认错题数
    assert data["S01"][5] != "—"                  # TOP 错因非空（桩命中 B01）
    assert data["S02"][5] == "—"                  # 无错题学生占位
    assert data["S01"][6] == "1" and data["S01"][7] == "85.0"   # 批改次数 / 平均分
    assert "排名" not in r.text


# ---------------------------------------------------------------- 学生错题档案趋势与报告（v4.13）

def test_student_profile_monthly_trend(isolated_db):
    """档案月度趋势：按月聚合总数与五大组分布，确认口径 + 改判优先。"""
    from app import db as appdb
    client.post("/api/students", json={"student_code": "S01", "name_local": "测试甲",
                                      "class_name": "九(1)班"})
    data = [
        ("2026-07-05 10:00:00", "B01", True),
        ("2026-07-20 10:00:00", "B01", True),    # 这条稍后改判 D04
        ("2026-08-10 10:00:00", "A01", True),
        ("2026-08-15 10:00:00", "C03", False),   # 未确认：不入趋势
    ]
    eids = []
    for created, cid, confirmed in data:
        eid = appdb.insert_error({"student_code": "S01", "class_name": "九(1)班",
                                  "question": "Q " + cid, "answer": "a",
                                  "category_id": cid, "created_at": created})
        if confirmed:
            appdb.confirm_error(eid, "accept")
        eids.append(eid)
    appdb.confirm_error(eids[1], "modify", override="D04")   # 改判优先：B01 → D04

    p = appdb.student_profile("S01")
    assert p["total_confirmed"] == 3
    tr = {t["ym"]: t for t in p["trend"]}
    assert set(tr) == {"2026-07", "2026-08"}          # 未确认那条不进趋势
    assert tr["2026-07"]["total"] == 2
    assert tr["2026-07"]["gB"] == 1 and tr["2026-07"]["gD"] == 1   # 改判后 B-1、D+1
    assert tr["2026-08"]["total"] == 1 and tr["2026-08"]["gA"] == 1

    # API 返回同源
    body = client.get("/api/students/S01/profile").json()
    assert len(body["trend"]) == 2


def test_student_report_and_zip(isolated_db):
    """错题档案报告：单生打印网页（趋势表）+ 全班 zip 每生一文件；404 / 空班 400。"""
    import io as _io
    import zipfile as _zip
    from app import db as appdb
    client.post("/api/students", json={"student_code": "S01", "name_local": "张三",
                                      "class_name": "九(1)班"})
    client.post("/api/students", json={"student_code": "S02", "name_local": "李四",
                                      "class_name": "九(1)班"})
    for i, created in enumerate(["2026-07-05 10:00:00", "2026-08-10 10:00:00"]):
        eid = appdb.insert_error({"student_code": "S01", "class_name": "九(1)班",
                                  "question": "Q%d I have seen the film yesterday." % i,
                                  "answer": "have seen", "category_id": "B01",
                                  "created_at": created})
        appdb.confirm_error(eid, "accept")

    r = client.get("/api/students/S01/report")
    assert r.status_code == 200
    assert "张三" in r.text and "月度趋势" in r.text and "2026-07" in r.text
    assert "不构成学生评价" in r.text                      # 合规脚标

    assert client.get("/api/students/S99/report").status_code == 404

    r2 = client.get("/api/students/reports.zip", params={"class_name": "九(1)班"})
    assert r2.status_code == 200
    zf = _zip.ZipFile(_io.BytesIO(r2.content))
    names = zf.namelist()
    assert len(names) == 2                                # 每生一个文件
    assert any("张三" in n for n in names) and any("李四" in n for n in names)
    s01 = next(n for n in names if "S01" in n)
    assert "月度趋势" in zf.read(s01).decode("utf-8")

    # 空班级（没有学生）→ 400
    assert client.get("/api/students/reports.zip",
                      params={"class_name": "不存在的班"}).status_code == 400
