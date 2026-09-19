"""补偿练习：LLM 生成（桩注入）、AI 标注、阅读题 passage、审校流转。"""
from app import practice
from app.providers import llm as llm_mod


def test_generate_via_llm_source():
    r = practice.generate("A01")
    assert r["practice_id"] > 0
    assert r["source"] == "llm"
    assert len(r["items"]) == 3
    for it in r["items"]:
        assert it["q"]
        assert it["answer"]
        assert it["ai_tag"] == "AI 生成·待审校"  # 合规标注


def test_generated_reading_item_keeps_passage():
    """阅读理解类练习保留 passage（题型差异化呈现）。"""
    r = practice.generate("C03")
    reading = [it for it in r["items"] if it.get("passage")]
    assert reading, "阅读练习应含短文原文 passage"


def test_generate_fails_fast_without_llm(monkeypatch):
    """LLM 网络失败显式报错（无 mock 降级、无静默回退）。"""
    def boom(messages, **kwargs):
        raise llm_mod.LLMError("网络不可达")
    monkeypatch.setattr(llm_mod, "chat", boom)
    try:
        practice.generate("A01")
    except ValueError as exc:
        assert "AI 生成失败" in str(exc)
    else:
        raise AssertionError("LLM 失败应显式报错")


def test_invalid_category_rejected():
    try:
        practice.generate("Z99")
    except ValueError:
        pass
    else:
        raise AssertionError("非法类别应被拒绝")


def test_draft_then_approve_flow(isolated_db):
    r = practice.generate("B02")
    pid = r["practice_id"]
    drafts = isolated_db.list_practices(status="draft")
    assert any(p["id"] == pid for p in drafts)
    assert practice.approve(pid) is True
    approved = isolated_db.list_practices(status="approved")
    assert any(p["id"] == pid for p in approved)
    assert practice.approve(pid) is False  # 重复审校无效


def test_generate_with_date_scope_injects_real_examples(isolated_db):
    """时间范围生成：范围内的真实错例注入提示词，scope 随练习留档（v4.12）。"""
    for i, created in enumerate(["2020-01-05 10:00:00", None]):  # 一条很久以前 + 一条现在
        eid = isolated_db.insert_error({
            "student_code": "S01", "class_name": "9年级1班",
            "question": "Q%d I have seen the film yesterday." % i,
            "answer": "have seen", "category_id": "B01", "created_at": created,
        })
        isolated_db.confirm_error(eid, "accept")

    # 只统计 2024-01-01 之后：注入 1 条错例，时间标签随练习留档
    r = practice.generate("B01", class_name="9年级1班",
                          date_from="2024-01-01", date_to="")
    assert r["real_example_n"] == 1
    assert r["scope"] == "2024-01-01 ~ 今"
    row = [p for p in isolated_db.list_practices() if p["id"] == r["practice_id"]][0]
    assert row["scope"] == "2024-01-01 ~ 今"

    # 不设范围：取全部错例，scope 留空
    r2 = practice.generate("B01", class_name="9年级1班")
    assert r2["real_example_n"] == 2 and r2["scope"] == ""

    # 范围外（未来）：无真实错例也能生成（退回本体库通用示例），不因无错例拒绝
    r3 = practice.generate("B01", date_from="2099-01-01")
    assert r3["real_example_n"] == 0 and r3["items"]
