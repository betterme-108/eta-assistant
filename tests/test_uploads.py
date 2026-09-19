"""上传管理：命名解析 → 分组预检 → 按学生解析 → 批改落库与文件关联。

命名规范：班级-批改类型-唯一标识（或两段式 班级-唯一标识，批改类型取当前
批改页）/ 学生姓名 / 照片。
定位链：班级+批改类型+唯一标识 → 一次提交（check_sessions.batch_no）；
学生姓名 → 一个人（paper_files.student_name）；学生/文件名 → 一张照片（paper_files.file_name）。
错题通过 source（唯一标识/学生/文件名）与 session_id 双向关联到上传文件与批改记录。
"""
import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import uploads

client = TestClient(app)


def _b64(marker: str) -> str:
    """可反解标记的假图片：OCR 桩按 marker 返回不同转录文本。"""
    return base64.b64encode(("img-%s" % marker).encode()).decode()


# ---------------------------------------------------------------- 命名解析（权威规则）

def test_parse_batch_folder_real_names():
    m = uploads.parse_batch_folder("九年级5班-考试试卷-半期考试")
    assert m["class_name"] == "九年级5班"
    assert m["exam_type"] == "考试试卷"
    assert m["batch_no"] == "半期考试"
    assert m["title"] == "半期考试"
    assert m["warnings"] == []

    m = uploads.parse_batch_folder("九年级5班-课时作业-1单元-2课时")
    assert m["exam_type"] == "课时作业"
    assert m["batch_no"] == "1单元-2课时"      # 第 3 段可含连字符，整体作唯一标识
    assert m["unit"] == "Unit 1"               # 唯一标识中的单元号自动解析

    m = uploads.parse_batch_folder("九年级5班-考试试卷-第一次月考")
    assert m["batch_no"] == "第一次月考"
    assert m["unit"] == ""

    # 两段式「班级-唯一标识」：批改类型取当前批改页（页面即类型，无需提示）
    m = uploads.parse_batch_folder("9年级20班-1单元2课时", "课时作业")
    assert m["class_name"] == "9年级20班"
    assert m["exam_type"] == "课时作业"
    assert m["batch_no"] == "1单元2课时"
    assert m["unit"] == "Unit 1"
    assert m["warnings"] == []

    # 两段式无页面信息：从唯一标识推断保底（考试特征优先，「单元测试」属考试）
    m = uploads.parse_batch_folder("九年级5班-半期考试")
    assert m["exam_type"] == "考试试卷"
    assert m["batch_no"] == "半期考试"
    assert m["warnings"]
    assert uploads.parse_batch_folder("九年级5班-单元测试")["exam_type"] == "考试试卷"


def test_parse_batch_folder_rejects_and_normalizes():
    # 两段式第 2 段就是批改类型：缺唯一标识，报错并建议补全
    with pytest.raises(ValueError) as ei:
        uploads.parse_batch_folder("九年级5班-课时作业")
    assert "唯一标识" in str(ei.value)

    # 两段式且无页面信息、名称认不出类型：报错请改名标明
    with pytest.raises(ValueError) as ei:
        uploads.parse_batch_folder("九年级5班-英语")
    assert "课时作业" in str(ei.value)

    # 批改类型轻度容错：含「考」归一为考试试卷 + 警告
    m = uploads.parse_batch_folder("九年级5班-月考-第一次")
    assert m["exam_type"] == "考试试卷"
    assert m["warnings"]

    # 第 2 段完全不是批改类型
    with pytest.raises(ValueError):
        uploads.parse_batch_folder("九年级5班-英语-半期考试")


def test_organize_groups_and_page_order():
    root = "九年级5班-考试试卷-半期考试"
    r = uploads.organize([
        "%s/林一诺/p2.jpg" % root,
        "%s/林一诺/p10.jpg" % root,     # 自然排序：p2 在 p10 前（页序）
        "%s/祝福/p1.jpg" % root,
        "%s/散图.jpg" % root,            # 根下散图 → 警告
        "%s/备注.txt" % root,            # 非图片 → 忽略
    ])
    assert [g["name"] for g in r["groups"]] == ["林一诺", "祝福"]
    assert r["groups"][0]["files"] == ["林一诺/p2.jpg", "林一诺/p10.jpg"]
    assert any("学生" in w for w in r["warnings"])


def test_organize_hard_errors():
    with pytest.raises(ValueError):     # 多个批次根：一次只能一个批次
        uploads.organize(["A班-考试试卷-期中/张三/1.jpg",
                          "B班-考试试卷-期中/李四/1.jpg"])
    with pytest.raises(ValueError):     # 根名认不出批改类型且无页面信息
        uploads.organize(["九年级5班-英语/张三/1.jpg"])
    with pytest.raises(ValueError):     # 没有学生子文件夹
        uploads.organize(["九年级5班-考试试卷-期中/1.jpg",
                          "九年级5班-考试试卷-期中/2.jpg"])


# ---------------------------------------------------------------- 上传预检端点

def test_organize_upload_endpoint_prefills_meta():
    root = "九年级5班-课时作业-1单元2课时"
    r = client.post("/api/check/organize-upload", json={
        "paths": ["%s/林一诺/1.jpg" % root, "%s/祝福/2.jpg" % root]})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["class_name"] == "九年级5班"
    assert body["meta"]["exam_type"] == "课时作业"
    assert body["meta"]["batch_no"] == "1单元2课时"
    assert body["meta"]["unit"] == "Unit 1"
    assert len(body["groups"]) == 2
    assert {g["name"] for g in body["groups"]} == {"林一诺", "祝福"}

    # 两段式批次根：批改类型取当前批改页（exam_type），学生照常映射
    r = client.post("/api/check/organize-upload", json={
        "paths": ["9年级20班-1单元2课时/林一诺/1.jpg",
                  "9年级20班-1单元2课时/陆思远/2.jpg"],
        "exam_type": "课时作业"})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["class_name"] == "9年级20班"
    assert body["meta"]["exam_type"] == "课时作业"
    assert body["meta"]["batch_no"] == "1单元2课时"
    assert {g["name"] for g in body["groups"]} == {"林一诺", "陆思远"}
    assert body["warnings"] == []       # 页面即类型：无需提示

    # 命名不合规 → 400 + 改名建议
    r = client.post("/api/check/organize-upload",
                    json={"paths": ["九年级5班-课时作业/林一诺/1.jpg"]})
    assert r.status_code == 400
    assert "唯一标识" in r.json()["detail"]


# ---------------------------------------------------------------- 按学生分组解析

def test_photo_parse_groups_merge_and_sources(monkeypatch):
    from app.providers import ocr as ocr_mod

    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "林一诺", "class": "九年级5班"}]})

    pages = {  # 转录桩：按图片标记返回不同页的内容
        "p1": "姓名：林一诺\nQ|1|单选|He ____ TV every day.|watch|watches",
        "p2": "Q|2|判断题|There is a book on the desk. ( )|T|T",
        "z1": "姓名：王五\nQ|1|单选|He ____ TV every day.|watched|watches",
    }

    def fake_ocr(b64):
        return pages[base64.b64decode(b64).decode()[4:]]

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr)
    root = "九年级5班-考试试卷-半期考试"
    r = client.post("/api/check/photo-parse", json={"items": [
        {"path": root + "/林一诺/p1.jpg", "image_base64": _b64("p1")},
        {"path": root + "/林一诺/p2.jpg", "image_base64": _b64("p2")},
        {"path": root + "/祝福/z1.jpg", "image_base64": _b64("z1")},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["batch_no"] == "半期考试"
    papers = body["papers"]
    assert len(papers) == 2
    lmy = next(p for p in papers if p["name"] == "林一诺")
    zf = next(p for p in papers if p["name"] == "祝福")
    # 学生姓名优先取文件夹名，并匹配名册（比 OCR 更可靠）
    assert lmy["matched"] is True and lmy["student_code"] == "S01"
    assert lmy["name_source"] == "folder"
    # 一名学生两张照片：同题号合并成两题，每题携带来源照片
    assert [q["no"] for q in lmy["questions"]] == [1, 2]
    assert lmy["questions"][0]["student_answer"] == "watch"
    assert lmy["questions"][0]["file"] == "林一诺/p1.jpg"
    assert lmy["questions"][1]["file"] == "林一诺/p2.jpg"
    # 名册外学生保持未匹配（老师核对时手动选）
    assert zf["matched"] is False
    assert zf["questions"][0]["student_answer"] == "watched"


def test_photo_parse_workbook_sections_renumber(monkeypatch):
    """书本课时作业：每个大题从 1 重编号（I-1/II-1/IV-1…），须按大题合并不互踩，
    且跨照片同大题字段互补（完形原文在上一张、作答/选项在下一张是常态）。"""
    from app.providers import ocr as ocr_mod

    pages = {
        "w1": ("Q|1|词语运用|（备选：tourist attraction / used to work / …）"
               "great changes ____ in Mr Yan's hometown."
               "|have taken place|have taken place|I 教材回顾\n"
               "Q|1|词语运用|There will be another two ____(高科技的) hospitals."
               "|high-tech|high-tech|Ⅱ 根据所给提示填写单词\n"
               "Q|1|完形填空|第 1 空|||IV 完形填空"),
        "w2": ("Q|1|完形填空|第 1 空|A|A|完形填空\n"
               "Q|2|完形填空|第 2 空|C|C|完形填空\n"
               "Q|1|阅读理解|What can we learn?|D|D|V 阅读理解"),
    }

    def fake_ocr(b64):
        return pages[base64.b64decode(b64).decode()[4:]]

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr)
    root = "九年级20班-课时作业-1单元2课时"
    r = client.post("/api/check/photo-parse", json={"items": [
        {"path": root + "/林一诺/w1.jpg", "image_base64": _b64("w1")},
        {"path": root + "/林一诺/w2.jpg", "image_base64": _b64("w2")},
    ]})
    assert r.status_code == 200
    qs = r.json()["papers"][0]["questions"]
    # I-1 / II-1 / IV-1 / IV-2 / V-1 → 全局重编号 1..5（II-1 不再被 I-1 覆盖丢失）
    assert [q["no"] for q in qs] == [1, 2, 3, 4, 5]
    assert qs[0]["section"] == "I 教材回顾"
    assert "another two" in qs[1]["question"]      # Ⅱ 大题的题仍在
    cloze1 = qs[2]
    assert cloze1["section"] == "IV 完形填空"      # 带标题一侧的 section 保留
    assert cloze1["student_answer"] == "A"         # w2 的作答补进 w1 的 (IV,1)
    assert cloze1["file"].endswith("w2.jpg")       # 来源照片随作答更新
    assert qs[4]["section"] == "V 阅读理解"


def test_photo_parse_reversed_page_order(monkeypatch):
    """学生把后一页先拍（文件名序 = 页序反了）：无标题照片推断的 section 不带
    序号，与带标题照片合并后应补回序号，并按书本大题顺序（I→IV→V）输出。"""
    from app.providers import ocr as ocr_mod

    pages = {  # r1 = 后一页（先拍）：完形选项 + 阅读题，无大题标题
        "r1": ("Q|1|完形填空|第 1 空|A|A|完形填空\n"
               "Q|2|完形填空|第 2 空|C|C|完形填空\n"   # 题号与带标题侧不重叠
               "Q|1|阅读理解|What's the best title?|B|B|阅读理解"),
        # r2 = 带标题的前一页（后拍）
        "r2": ("Q|1|词语运用|great changes ____ in Mr Yan's hometown."
               "|have taken place|have taken place|I 教材回顾\n"
               "Q|1|完形填空|第 1 空（原文见 passage）|||IV 完形填空\n"
               "Q|1|阅读理解|What's the best title?||B|V 阅读理解"),
    }

    def fake_ocr(b64):
        return pages[base64.b64decode(b64).decode()[4:]]

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr)
    root = "九年级20班-课时作业-1单元2课时"
    r = client.post("/api/check/photo-parse", json={"items": [
        {"path": root + "/陆思远/r1.jpg", "image_base64": _b64("r1")},
        {"path": root + "/陆思远/r2.jpg", "image_base64": _b64("r2")},
    ]})
    assert r.status_code == 200
    qs = r.json()["papers"][0]["questions"]
    # 书本顺序：教材回顾(I) → 完形(IV) → 阅读(V)，而不是拍摄顺序（完形、阅读在前）
    assert [q["section"] for q in qs] == ["I 教材回顾", "IV 完形填空",
                                          "IV 完形填空", "V 阅读理解"]
    assert [q["no"] for q in qs] == [1, 2, 3, 4]
    # 题号不重叠、未走到合并路径的题，section 也统一为带序号版本（前端不拆分组）
    assert qs[2]["question"] == "第 2 空" and qs[2]["student_answer"] == "C"
    # r1 先插入的作答不丢；r2 的 passage/参考答案字段补进
    assert qs[1]["student_answer"] == "A" and qs[1]["file"].endswith("r1.jpg")
    assert qs[3]["student_answer"] == "B" and qs[3]["suggested_answer"] == "B"


def test_photo_parse_partial_ocr_failure(monkeypatch):
    from app.providers import ocr as ocr_mod

    def fake_ocr(b64):
        marker = base64.b64decode(b64).decode()[4:]
        if marker == "bad":
            raise ocr_mod.OCRError("识别失败")
        return "Q|1|判断题|There is a book on the desk. ( )|T|T"

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr)
    root = "九年级5班-考试试卷-期中"
    r = client.post("/api/check/photo-parse", json={"items": [
        {"path": root + "/张三/bad.jpg", "image_base64": _b64("bad")},
        {"path": root + "/张三/ok.jpg", "image_base64": _b64("ok")},
    ]})
    assert r.status_code == 200
    p = r.json()["papers"][0]
    assert p["failed_files"] == ["张三/bad.jpg"]     # 单张失败不阻断其余照片
    assert len(p["questions"]) == 1
    assert p["ocr_error"] == ""


def test_photo_parse_rejects_legacy_images_base64():
    """散图直传已移除：只接受带路径的批次文件夹照片。"""
    assert client.post("/api/check/photo-parse",
                       json={"images_base64": [_b64("x")]}).status_code == 422


# ---------------------------------------------------------------- 批改落库与文件关联（定位链闭环）

def test_assignment_registers_files_and_links_errors():
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "林一诺", "class": "九年级5班"}]})
    r = client.post("/api/check/assignment", json={
        "key": [{"no": 1, "qtype": "单选", "question": "He ____ TV every day.",
                 "answer": "watches", "score": 1}],
        "answers": [{
            "student_code": "S01", "name": "林一诺",
            "files": ["林一诺/p1.jpg"],
            "items": [{"no": 1, "answer": "watch", "file": "林一诺/p1.jpg"}],
        }],
        "class_name": "九年级5班", "exam_type": "考试试卷",
        "batch_no": "半期考试", "folder": "九年级5班-考试试卷-半期考试",
    })
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    assert body["title"] == "半期考试"                      # 标题默认取唯一标识
    assert body["meta"]["batch_no"] == "半期考试"
    assert body["meta"]["folder"] == "九年级5班-考试试卷-半期考试"
    assert body["results"][0]["files"] == ["林一诺/p1.jpg"]
    assert "inserted_error_ids" not in body                 # 内部字段不随报告外露

    # 错题双关联：source（唯一标识/学生/文件名）+ session_id（批改记录）
    errs = client.get("/api/errors", params={"confirmed": False}).json()
    assert len(errs) == 1
    assert errs[0]["source"] == "半期考试/林一诺/p1.jpg"
    assert errs[0]["session_id"] == sid

    # 上传文件登记：一次提交 → 学生 → 照片
    files = client.get("/api/check/sessions/%d/files" % sid).json()["files"]
    assert len(files) == 1
    assert files[0]["file_name"] == "林一诺/p1.jpg"
    assert files[0]["student_name"] == "林一诺"
    assert files[0]["student_code"] == "S01"
    assert files[0]["batch_no"] == "半期考试"

    # 历史列表带唯一标识（定位每次提交）
    sessions = client.get("/api/check/sessions").json()["sessions"]
    assert sessions[0]["batch_no"] == "半期考试"


def test_assignment_batch_no_falls_back_to_folder():
    """batch_no 未填时从批次文件夹名解析（前端漏传也不断链）。"""
    r = client.post("/api/check/assignment", json={
        "key": [{"no": 1, "qtype": "单选", "question": "He ____ TV every day.",
                 "answer": "watches", "score": 1}],
        "answers": [{"student_code": "", "items": [{"no": 1, "answer": "watch"}]}],
        "class_name": "九年级5班", "exam_type": "考试试卷",
        "folder": "九年级5班-考试试卷-第一次月考",
    })
    assert r.status_code == 200
    sid = r.json()["session_id"]
    s = client.get("/api/check/sessions/%d" % sid).json()
    assert s["batch_no"] == "第一次月考"
    assert s["report"]["meta"]["batch_no"] == "第一次月考"


# ---------------------------------------------------------------- 作业/考试两页隔离

def _submit_assignment(exam_type, title):
    """提交一份带错题的作业批改（返回 session_id，错题用于删除关联验证）。"""
    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "林一诺", "class": "九年级5班"}]})
    r = client.post("/api/check/assignment", json={
        "key": [{"no": 1, "qtype": "单选", "question": "He ____ TV every day.",
                 "answer": "watches", "score": 1}],
        "answers": [{"student_code": "S01",
                      "items": [{"no": 1, "answer": "watch"}]}],   # 必错 → 错题入库
        "class_name": "九年级5班", "exam_type": exam_type, "title": title})
    assert r.status_code == 200
    return r.json()["session_id"]


def test_sessions_filtered_by_exam_type():
    """作业/考试两页数据隔离：exam_type=考试试卷 只取考试，
    课时作业取其余一切（含 NULL/空旧数据），两页互不可见。"""
    _submit_assignment("考试试卷", "第一次月考")
    _submit_assignment("课时作业", "1单元2课时")
    exam = client.get("/api/check/sessions",
                      params={"kind": "assignment", "exam_type": "考试试卷"}).json()["sessions"]
    hw = client.get("/api/check/sessions",
                    params={"kind": "assignment", "exam_type": "课时作业"}).json()["sessions"]
    assert [s["title"] for s in exam] == ["第一次月考"]
    assert "第一次月考" not in [s["title"] for s in hw]
    assert hw[0]["title"] == "1单元2课时"


def test_delete_session_keeps_errors_by_default():
    """删除批改历史：默认保留错题（仅解除 session 关联）；
    purge_errors=true 连错题删；照片登记一并清理；不存在 404。"""
    sid = _submit_assignment("课时作业", "待删课时")
    assert client.get("/api/check/sessions/%d/files" % sid).status_code == 200

    d = client.delete("/api/check/sessions/%d" % sid)
    assert d.status_code == 200
    assert d.json() == {"ok": True, "removed_errors": 1, "purged": False}
    assert client.get("/api/check/sessions/%d" % sid).status_code == 404
    assert client.get("/api/check/sessions/%d/files" % sid).status_code == 404   # 照片登记随删
    errs = client.get("/api/errors", params={"confirmed": False}).json()
    assert len(errs) == 1 and errs[0]["session_id"] is None   # 错题保留、仅解除关联

    sid2 = _submit_assignment("课时作业", "连错题删")
    d2 = client.delete("/api/check/sessions/%d" % sid2,
                       params={"purge_errors": True})
    assert d2.json()["purged"] is True and d2.json()["removed_errors"] == 1
    # purge 只删 sid2 本次收集的错题；sid1 保留的那条（已解除关联）不受影响
    errs2 = client.get("/api/errors", params={"confirmed": False}).json()
    assert len(errs2) == 1 and errs2[0]["session_id"] is None

    assert client.delete("/api/check/sessions/999999").status_code == 404


# ---------------------------------------------------------------- 右上角班级联动与原文编辑重解析

def test_photo_parse_class_name_limits_roster_match(monkeypatch):
    """批改页右上角选择班级后，名册匹配只在该班学生里找：
    同名学生在别的班不误配；本班学生照常匹配。"""
    from app.providers import ocr as ocr_mod

    client.post("/api/students/import", json={
        "rows": [{"code": "S01", "name": "林一诺", "class": "九年级5班"},
                 {"code": "S02", "name": "陆思远", "class": "九年级20班"}]})

    def fake_ocr(b64):
        return "姓名：林一诺\nQ|1|单选|He ____ TV every day.|watched|watches"

    monkeypatch.setattr(ocr_mod, "ocr_page", fake_ocr)
    root = "9年级20班-1单元2课时"
    # 右上角选的 6 班：6 班名册没有林一诺 → 不匹配（尽管 5 班有同名名册）
    r = client.post("/api/check/photo-parse", json={
        "items": [{"path": root + "/林一诺/p1.jpg", "image_base64": _b64("p1")}],
        "class_name": "九年级20班"})
    assert r.status_code == 200
    p = r.json()["papers"][0]
    assert p["matched"] is False and p["student_code"] == ""
    # 右上角选的 5 班：名册命中
    r = client.post("/api/check/photo-parse", json={
        "items": [{"path": root + "/林一诺/p1.jpg", "image_base64": _b64("p1")}],
        "class_name": "九年级5班"})
    p = r.json()["papers"][0]
    assert p["matched"] is True and p["student_code"] == "S01"


def test_md_reparse_endpoint(monkeypatch):
    """原文编辑后重新结构化端点：按批改类型重新解析出题目，空原文拒绝。"""
    from app.services import photo_check

    monkeypatch.setattr(photo_check, "_md_questions",
        lambda md, exam_type="": {"name": "", "questions": [
            {"no": 1, "qtype": "单选",
             "question": "He ____ TV every day.", "student_answer": "watch",
             "suggested_answer": "watches", "options": [], "section": "",
             "passage": ""}]})
    r = client.post("/api/check/md-reparse", json={
        "exam_type": "课时作业", "markdown": "一、选择填空\n1. He ____ TV every day."})
    assert r.status_code == 200
    qs = r.json()["questions"]
    assert len(qs) == 1 and qs[0]["no"] == 1
    assert qs[0]["suggested_answer"] == "watches"
    # 空原文 → 400
    assert client.post("/api/check/md-reparse",
                       json={"markdown": "  "}).status_code == 400


def test_sessions_filtered_by_class_name():
    """批改历史跟随右上角班级过滤：不同班的记录互不可见；不传班级 = 全部。"""
    for i, (cls, title) in enumerate(
            [("九年级5班", "5班作业"), ("九年级20班", "6班作业")]):
        client.post("/api/students/import", json={
            "rows": [{"code": "S0%d" % (i + 1), "name": "林一诺", "class": cls}]})
        r = client.post("/api/check/assignment", json={
            "key": [{"no": 1, "qtype": "单选", "question": "He ____ TV every day.",
                     "answer": "watches", "score": 1}],
            "answers": [{"student_code": "S0%d" % (i + 1),
                          "items": [{"no": 1, "answer": "watches"}]}],
            "class_name": cls, "exam_type": "课时作业", "title": title})
        assert r.status_code == 200
    c5 = client.get("/api/check/sessions", params={
        "kind": "assignment", "class_name": "九年级5班"}).json()["sessions"]
    c6 = client.get("/api/check/sessions", params={
        "kind": "assignment", "class_name": "九年级20班"}).json()["sessions"]
    assert [s["title"] for s in c5] == ["5班作业"]
    assert [s["title"] for s in c6] == ["6班作业"]
    all_s = client.get("/api/check/sessions",
                       params={"kind": "assignment"}).json()["sessions"]
    assert len(all_s) == 2
