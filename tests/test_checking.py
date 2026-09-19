"""批改引擎与批改 API：听写批改 + 作业/考试批改（客观题规则判卷、主观题 AI 判定、
书面表达分维评分）→ 错题自动进入待确认 → 批改历史留存。

题型边界：听力等音频类题目不在本系统范围（无 ASR/TTS 链路）。
"""
import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app
from app.services import checking

client = TestClient(app)


# ---------------------------------------------------------------- 判定基础规则

def test_normalize_answer():
    assert checking.normalize_answer("  Hello, World! ") == "helloworld"
    assert checking.normalize_answer("ＡＢＣ１２３") == "abc123"   # 全角归一
    assert checking.normalize_answer("She's here.") == "sheshere"
    assert checking.normalize_answer("") == ""
    assert checking.normalize_answer(None) == ""


def test_edit_distance():
    assert checking.edit_distance("cat", "cat") == 0
    assert checking.edit_distance("cat", "bat") == 1
    assert checking.edit_distance("", "abc") == 3
    assert checking.edit_distance("kitten", "sitting") == 3


def test_word_verdict():
    assert checking.word_verdict("apple", "apple")[0] == "correct"
    assert checking.word_verdict("Apple!", "apple")[0] == "correct"
    assert checking.word_verdict("", "apple")[0] == "blank"
    # 短词差 1 字母 → 拼写有误；差 2 字母 → 写成了别的词
    assert checking.word_verdict("aple", "apple")[0] == "misspell"
    assert checking.word_verdict("appel", "apple")[0] == "wrong"
    # 长词（≥7 字母）差 2 字母仍视为拼写有误
    assert checking.word_verdict("difficlt", "difficult")[0] == "misspell"


# ---------------------------------------------------------------- 听写批改 API

def test_dictation_check_end_to_end():
    words = [{"no": 1, "en": "apple", "zh": "苹果"},
             {"no": 2, "en": "banana", "zh": "香蕉"},
             {"no": 3, "en": "difficult", "zh": "困难的"}]
    answers = [
        {"student_code": "S01", "items": ["apple", "banan", ""]},
        {"student_code": "S02", "items": ["apple", "banana", "difficult"]},
    ]
    r = client.post("/api/check/dictation", json={
        "words": words, "answers": answers, "unit": "Unit 5", "week": 12,
        "class_name": "一班", "title": "第五单元听写"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] > 0
    assert body["kind"] == "dictation"
    assert body["title"] == "第五单元听写"
    assert body["stats"]["students"] == 2
    assert body["stats"]["total_words"] == 3
    assert body["stats"]["wrong_items"] == 2
    assert body["stats"]["inserted_errors"] == 2
    assert body["stats"]["uncertain"] == 0

    s1 = next(x for x in body["results"] if x["student_code"] == "S01")
    s2 = next(x for x in body["results"] if x["student_code"] == "S02")
    assert s1["correct_n"] == 1 and s1["score"] == 33.3
    assert s2["correct_n"] == 3 and s2["score"] == 100.0
    v1 = {w["no"]: w["verdict"] for w in s1["items"]}
    assert v1 == {1: "correct", 2: "misspell", 3: "blank"}

    # 错词自动入库（待确认），且归因经听写桩：拼写有误 → A01，空白 → E04
    pending = client.get("/api/errors", params={"confirmed": False}).json()
    assert len(pending) == 2
    by_q = {e["question"]: e for e in pending}
    assert by_q["听写：中文「香蕉」，应写英文「banana」"]["category_id"] == "A01"
    assert by_q["听写：中文「困难的」，应写英文「difficult」"]["category_id"] == "E04"
    assert all(e["qtype"] == "听写" for e in pending)
    assert all(e["student_code"] == "S01" for e in pending)

    # 批改历史：概要与详情均可见
    sessions = client.get("/api/check/sessions", params={"kind": "dictation"}).json()
    assert sessions["sessions"][0]["id"] == body["session_id"]
    assert sessions["sessions"][0]["students"] == 2
    detail = client.get("/api/check/sessions/%d" % body["session_id"]).json()
    assert detail["report"]["stats"]["wrong_items"] == 2
    assert detail["summary"]["avg_score"] == 66.7


# ---------------------------------------------------------------- 作业/考试批改 API

def test_assignment_check_end_to_end():
    key = [
        {"no": 1, "question": "Choose the correct answer: I ____ to school yesterday.",
         "qtype": "单选", "answer": "went", "score": 2},
        {"no": 2, "question": "完成句子：I ____ a student.（用 be 动词填空）",
         "qtype": "完成句子", "answer": "am", "score": 3},
        {"no": 3, "question": "书面表达：请以 My Weekend 为题写一篇短文（15 分）。",
         "qtype": "书面表达", "answer": "（参考范文）", "score": 15},
    ]
    answers = [
        {"student_code": "S01", "items": [
            {"no": 1, "answer": "went"},
            {"no": 2, "answer": "am"},
            {"no": 3, "answer": "I went to the park with my parents last weekend. "
                                   "We flew kites and had a picnic. It was a happy day."}]},
        {"student_code": "S02", "items": [
            {"no": 1, "answer": "go"},
            {"no": 2, "answer": "am"},
            {"no": 3, "answer": "我没写完作文"}]},
    ]
    r = client.post("/api/check/assignment", json={
        "key": key, "answers": answers, "unit": "Unit 5", "week": 12,
        "class_name": "一班", "title": "第五单元小测"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] > 0
    assert body["kind"] == "assignment"
    assert body["stats"]["students"] == 2
    assert body["stats"]["uncertain"] == 0
    assert body["stats"]["wrong_items"] == 2
    assert body["stats"]["inserted_errors"] == 2

    s1 = next(x for x in body["results"] if x["student_code"] == "S01")
    s2 = next(x for x in body["results"] if x["student_code"] == "S02")
    # S01 全对：2 + 3 + 15 = 20 分 → 100
    assert s1["correct_n"] == 3 and s1["score"] == 100.0
    assert s1["items"][2]["verdict"] == "correct"
    assert s1["items"][2]["rubric"]                # 作文带回分维评语
    # S02：单选错（规则判定，不调 AI）+ 作文"没写完"（桩给 40% → 6 分）
    assert s2["correct_n"] == 1
    assert s2["items"][0]["verdict"] == "wrong"
    assert s2["items"][0]["subjective"] is False
    assert s2["items"][2]["verdict"] == "wrong"
    assert s2["items"][2]["got"] == 6.0
    assert s2["items"][2]["subjective"] is True
    assert s2["score"] == 45.0                     # (3 + 6) / 20 * 100 → 45

    # 错题自动入库（待确认）：客观题 + 作文各一条，作文按分维评分扣分收集
    pending = client.get("/api/errors", params={"confirmed": False}).json()
    assert len(pending) == 2
    qtypes = sorted(e["qtype"] for e in pending)
    assert qtypes == ["书面表达", "单选"]
    essay = next(e for e in pending if e["qtype"] == "书面表达")
    assert essay["student_code"] == "S02"
    assert essay["answer"] == "我没写完作文"


def test_assignment_errors_carry_options_and_passage():
    """批改收集的错题携带选项与阅读原文：选择题错题在「错题确认」页能完整展示
    （题干、选项、原文、作答、标准答案齐全），归因时选项也随题干传入。"""
    key = [{
        "no": 1, "question": "Choose the best answer: I ____ to school yesterday.",
        "qtype": "单选", "answer": "went", "score": 2,
        "options": ["A. go", "B. went", "C. goes", "D. gone"],
    }, {
        "no": 2, "question": "What can we learn from the passage?",
        "qtype": "阅读理解", "answer": "B", "score": 2,
        "options": ["A. Tom waters it daily.", "B. The tree has grown for five years."],
        "passage": "Tom planted a small tree five years ago. Now it is taller.",
    }]
    answers = [{"student_code": "S01", "items": [
        {"no": 1, "answer": "A"}, {"no": 2, "answer": "A"}]}]
    r = client.post("/api/check/assignment", json={
        "key": key, "answers": answers, "class_name": "一班"})
    assert r.status_code == 200
    assert r.json()["stats"]["inserted_errors"] == 2
    errs = client.get("/api/errors", params={"confirmed": False}).json()
    by_q = {e["question"]: e for e in errs}
    e1 = by_q["Choose the best answer: I ____ to school yesterday."]
    assert e1["options"] == ["A. go", "B. went", "C. goes", "D. gone"]
    e2 = by_q["What can we learn from the passage?"]
    assert e2["options"] == ["A. Tom waters it daily.", "B. The tree has grown for five years."]
    assert e2["passage"] == "Tom planted a small tree five years ago. Now it is taller."


def test_assignment_uncertain_falls_to_teacher():
    """AI 判定失败时置「待判定」：不计分、不自动入库，交给教师把关。"""
    key = [{"no": 1, "question": "概要补全：The passage is mainly about ____.",
            "qtype": "概要补全", "answer": "the importance of reading", "score": 5}]
    answers = [{"student_code": "S01", "items": [{"no": 1, "answer": "reading is good（故障）"}]}]
    r = client.post("/api/check/assignment", json={"key": key, "answers": answers})
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["uncertain"] == 1
    assert body["stats"]["wrong_items"] == 0
    assert body["stats"]["inserted_errors"] == 0
    assert body["stats"]["avg_score"] is None        # 没有可计分题目
    assert body["results"][0]["score"] is None
    assert body["results"][0]["items"][0]["verdict"] == "uncertain"


def test_assignment_ordered_string_items():
    """作答 items 兼容按题号顺序的字符串列表形态。"""
    key = [{"no": 1, "question": "Q1", "qtype": "单选", "answer": "A", "score": 1},
           {"no": 2, "question": "Q2", "qtype": "单选", "answer": "B", "score": 1}]
    answers = [{"student_code": "S01", "items": ["A", "C"]}]
    r = client.post("/api/check/assignment", json={"key": key, "answers": answers})
    assert r.status_code == 200
    s1 = r.json()["results"][0]
    assert s1["items"][0]["verdict"] == "correct"
    assert s1["items"][1]["verdict"] == "wrong"
    assert s1["score"] == 50.0


def test_check_validation_errors():
    r = client.post("/api/check/dictation", json={"words": [], "answers": []})
    assert r.status_code == 400
    r = client.post("/api/check/assignment", json={"key": [], "answers": []})
    assert r.status_code == 400
    r = client.get("/api/check/sessions/999999")
    assert r.status_code == 404


# ---------------------------------------------------------------- 统一信息：写入与手动修改

def test_session_meta_and_update():
    words = [{"no": 1, "en": "apple", "zh": "苹果"}]
    answers = [{"student_code": "S01", "items": ["apple"]}]
    r = client.post("/api/check/dictation", json={
        "words": words, "answers": answers, "unit": "Unit 5", "week": 12,
        "class_name": "一班", "exam_type": "日常听写", "title": "第五单元听写"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["meta"]["exam_type"] == "日常听写"

    # 手动修改统一信息（只改班级与测试类型）
    r = client.patch("/api/check/sessions/%d" % sid, json={
        "class_name": "二班", "exam_type": "单元测试", "week": 13})
    assert r.status_code == 200
    body = r.json()
    assert body["class_name"] == "二班"
    assert body["week"] == 13
    assert body["exam_type"] == "单元测试"
    assert body["report"]["title"] == "第五单元听写"   # 未传字段不变
    assert body["report"]["meta"]["exam_type"] == "单元测试"

    # 列表概要可见修改后的信息
    sessions = client.get("/api/check/sessions", params={"kind": "dictation"}).json()
    top = sessions["sessions"][0]
    assert top["id"] == sid
    assert top["exam_type"] == "单元测试"


# ---------------------------------------------------------------- 按学生导出（含 AI 总结与建议）

def test_export_session_csv_with_feedback():
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "张三", "class": "一班"}]})
    words = [{"no": 1, "en": "apple", "zh": "苹果"}]
    answers = [{"student_code": "S01", "items": ["aple"]}]
    r = client.post("/api/check/dictation", json={
        "words": words, "answers": answers, "class_name": "一班", "title": "听写1"})
    sid = r.json()["session_id"]

    r = client.get("/api/check/sessions/%d/export" % sid)
    assert r.status_code == 200
    text = r.text
    assert "学号" in text and "姓名" in text and "AI 总结" in text and "学习建议" in text
    assert "S01" in text and "张三" in text
    assert "存在个别错误（测试桩）" in text    # AI 总结建议（桩）
    assert "逐题明细" not in text and "aple" not in text   # 导出不含逐题明细
    assert r.headers["content-type"].startswith("text/csv")

    r = client.get("/api/check/sessions/999999/export")
    assert r.status_code == 404


def test_export_session_all_students_zip():
    # 全部单生报告：每生一个独立 HTML，打包 zip 一次下载（与单生导出同源同口径）
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "张三", "class": "一班"},
                 {"code": "S02", "name": "李四", "class": "一班"}]})
    words = [{"no": 1, "en": "apple", "zh": "苹果"}]
    answers = [{"student_code": "S01", "items": ["aple"]},
               {"student_code": "S02", "items": ["apple"]}]
    r = client.post("/api/check/dictation", json={
        "words": words, "answers": answers, "class_name": "一班", "title": "听写2"})
    sid = r.json()["session_id"]

    r = client.get("/api/check/sessions/%d/export-all" % sid)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert len(names) == 2
    assert any("S01" in n for n in names) and any("S02" in n for n in names)
    html01 = zf.read([n for n in names if "S01" in n][0]).decode("utf-8")
    assert "批改报告" in html01 and "张三" in html01
    assert "存在个别错误（测试桩）" in html01    # AI 总结建议（桩）：与单生导出同源
    assert "aple" in html01                       # 逐题明细随文件携带

    r = client.get("/api/check/sessions/999999/export-all")
    assert r.status_code == 404
