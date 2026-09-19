"""归因引擎测试：LLM 解析（合法/低置信/非法重试/网络失败降级）、prompt 组装、E 类复核。"""
from app import classifier, db
from app.providers import llm as llm_mod


def _patch_chat_json(monkeypatch, payload):
    def fake(messages, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return payload
    monkeypatch.setattr(llm_mod, "chat_json", fake)


# ---------------- LLM 归因解析 ----------------

def test_llm_valid_json(monkeypatch):
    _patch_chat_json(monkeypatch, {
        "category_id": "D04", "evidence": "I very like English.",
        "teaching_point": "避免中式语序", "confidence": 0.9})
    r = classifier.classify("q", "I very like English.")
    assert r["source"] == "llm"
    assert r["category_id"] == "D04"
    assert r["needs_review"] is False


def test_llm_low_confidence_needs_review(monkeypatch):
    _patch_chat_json(monkeypatch, {
        "category_id": "A01", "evidence": "e", "teaching_point": "t", "confidence": 0.5})
    r = classifier.classify("q", "a")
    assert r["needs_review"] is True


def test_llm_invalid_category_retries_then_degrades(monkeypatch):
    """非法 category_id 重试 1 次仍非法 → 转教师判断（llm-error）。"""
    _patch_chat_json(monkeypatch, {
        "category_id": "Z99", "evidence": "e", "teaching_point": "t", "confidence": 0.9})
    r = classifier.classify("q", "a")
    assert r["source"] == "llm-error"
    assert r["category_id"] == ""
    assert r["needs_review"] is True


def test_llm_network_error_degrades_to_teacher(monkeypatch):
    """LLM 网络失败不阻断录入——降级为待教师确认。"""
    _patch_chat_json(monkeypatch, llm_mod.LLMError("网络不可达"))
    r = classifier.classify("q", "a")
    assert r["source"] == "llm-error"
    assert r["needs_review"] is True


def test_prompt_contains_qtype_and_passage(monkeypatch):
    """题型与阅读原文进入 prompt（C 类归因需结合原文定位证据）。"""
    captured = {}

    def fake(messages, **kwargs):
        captured["user"] = messages[-1]["content"]
        return {"category_id": "C03", "evidence": "e", "teaching_point": "t", "confidence": 0.8}

    monkeypatch.setattr(llm_mod, "chat_json", fake)
    classifier.classify("What can we infer?", "abs",
                        qtype="阅读理解", passage="Tom planted a tree five years ago.")
    assert "Tom planted a tree five years ago." in captured["user"]
    assert "阅读理解" in captured["user"]


# ---------------- E 类复核（第二次调用，确定性规则） ----------------

def _insert_confirmed(student, cat, q="What can we learn from the passage?", unit="Unit 1"):
    eid = db.insert_error({
        "student_code": student, "question": q, "answer": "x", "correct": "y",
        "qtype": "阅读理解", "unit": unit, "category_id": cat,
        "evidence": "t", "confidence": 0.9,
    })
    db.confirm_error(eid, "accept")
    return eid


def test_review_behavior_e05_unmastered():
    for i in range(2):
        _insert_confirmed("S07", "A01", q="Fill in the blank %d" % i)
    behavior = classifier.review_behavior("S07", "A01", "Fill in the blank new")
    assert behavior is not None
    assert behavior["tag"] == "E05"  # 历史 2 + 本次 1 = 3 次 → 未掌握
    assert behavior["student_cat_count"] == 3


def test_review_behavior_e02_careless():
    db.import_students([{"code": "S%02d" % i} for i in range(1, 31)])  # 30 人班级
    # 该题仅 S05 错 1 次 → 班级正确率 29/30 = 0.97
    behavior = classifier.review_behavior("S05", "A06", "Choose: ___ apple a day")
    assert behavior is not None
    assert behavior["tag"] == "E02"
    assert behavior["class_correct_rate"] >= 0.9


def test_review_behavior_skipped_for_e_category():
    assert classifier.review_behavior("S01", "E02", "question") is None
    assert classifier.review_behavior("S01", "", "question") is None
