# -*- coding: utf-8 -*-
"""智能体会话主路：请求构建（双鉴权）+ 错误归一 + 两段式解析 + 主路/降级 + 判定直入。

覆盖点（对应拍照批改「智能体会话主路」两段式改造的验收）：
  - agent_chat：chat 请求体与鉴权头（API Key Bearer / AK-SK 签名）、
    业务与网关错误归一、空 content 报错、extract_markdown 提取消息构建；
  - photo_check：两段式 _agent_page（智能体提取 Markdown → DeepSeek 结构化，
    空题过滤/题号兑底/qtype 白名单/作答置信度；结构化失败归一为
    AgentChatError）、_parse_page 主路切换与失败降级 OCR 路、
    parse_papers 跨照片大题合并（agent 路无页隔离）；
  - checking：预判 verdict 直入（correct/wrong/blank 覆盖规则比对、
    作文例外走 AI 分维、无 verdict 老链路不变、字符串列表形态兼容）。
"""
import json

import pytest

from app.core import config
from app.providers import agent_chat
from app.services import checking, photo_check


# ---------------------------------------------------------------- HTTP 桩

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeClient:
    """记录调用的假 httpx.Client；按序弹出响应，耗尽或入参为异常时抛错。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, content=None, headers=None, **kwargs):
        self.calls.append({"url": url, "content": content, "headers": headers})
        if not self.responses:
            raise RuntimeError("连接中断（测试桩）")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(content):
    return _FakeResp({"choices": [{"message": {"role": "assistant", "content": content},
                                     "finish_reason": "stop", "index": 0}]})


# ---------------------------------------------------------------- 请求构建与鉴权

def _enable_api_key(monkeypatch):
    monkeypatch.setattr(config, "AGENT_CHAT_BOT_ID", "bot1234567890abcdef")
    monkeypatch.setattr(config, "AGENT_CHAT_API_KEY_SECRET", "secret-xyz")
    monkeypatch.setattr(config, "AGENT_CHAT_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_SECRET_ACCESS_KEY", "")


def _enable_aksk(monkeypatch):
    monkeypatch.setattr(config, "AGENT_CHAT_BOT_ID", "bot1234567890abcdef")
    monkeypatch.setattr(config, "AGENT_CHAT_API_KEY_SECRET", "")
    monkeypatch.setattr(config, "AGENT_CHAT_ACCESS_KEY_ID", "AKLTtest")
    monkeypatch.setattr(config, "AGENT_CHAT_SECRET_ACCESS_KEY", "sk-test")


def test_chat_apikey_payload_and_auth(monkeypatch):
    """API Key 接入：Bearer 鉴权头 + ServiceName + bot_id/stream/messages 请求体。"""
    _enable_api_key(monkeypatch)
    fake = _FakeClient([_ok("你好")])
    monkeypatch.setattr(agent_chat, "_get_client", lambda: fake)

    out = agent_chat.chat([{"role": "user", "content": "你好"}])
    assert out == "你好"
    req = fake.calls[0]
    assert req["url"] == config.AGENT_CHAT_API_URL
    assert req["headers"]["Authorization"] == "Bearer secret-xyz"
    assert req["headers"]["ServiceName"] == "ask_echo"
    body = json.loads(req["content"])
    assert body["bot_id"] == "bot1234567890abcdef"
    assert body["stream"] is False
    assert body["messages"][0]["content"] == "你好"


def test_chat_aksk_signed_request(monkeypatch):
    """AK/SK 接入：TOP 网关 URL + X-Date/Authorization 签名头。"""
    _enable_aksk(monkeypatch)
    fake = _FakeClient([_ok("ok")])
    monkeypatch.setattr(agent_chat, "_get_client", lambda: fake)

    assert agent_chat.chat([{"role": "user", "content": "hi"}]) == "ok"
    req = fake.calls[0]
    assert req["url"] == config.AGENT_CHAT_AKSK_URL
    assert "Action=ChatCompletion" in req["url"] and "Version=2026-01-01" in req["url"]
    assert req["headers"]["Host"] == "mercury.volcengineapi.com"
    assert req["headers"]["X-Date"]                       # UTC 时间戳参与签名
    auth = req["headers"]["Authorization"]
    assert auth.startswith("HMAC-SHA256 Credential=AKLTtest/")
    assert "SignedHeaders=content-type;host;x-date" in auth
    assert "Signature=" in auth


def test_chat_unconfigured(monkeypatch):
    """凭据未配置：显式报错（fail-fast，不静默）。"""
    monkeypatch.setattr(config, "AGENT_CHAT_BOT_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_API_KEY_SECRET", "")
    monkeypatch.setattr(config, "AGENT_CHAT_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_SECRET_ACCESS_KEY", "")
    with pytest.raises(agent_chat.AgentChatError, match="未配置"):
        agent_chat.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- 错误归一

def test_chat_error_normalized(monkeypatch):
    """业务错误（body.error）与网关错误（ResponseMetadata.Error）→ AgentChatError。"""
    _enable_api_key(monkeypatch)
    fake = _FakeClient([_FakeResp({"error": {
        "code": "invalid_api_key", "message": "invalid api key",
        "type": "authentication_error"}})])
    monkeypatch.setattr(agent_chat, "_get_client", lambda: fake)
    with pytest.raises(agent_chat.AgentChatError, match="invalid_api_key"):
        agent_chat.chat([{"role": "user", "content": "hi"}])

    fake2 = _FakeClient([_FakeResp({"ResponseMetadata": {"Error": {
        "Code": "SignatureDoesNotMatch", "Message": "签名不匹配"}}})])
    monkeypatch.setattr(agent_chat, "_get_client", lambda: fake2)
    with pytest.raises(agent_chat.AgentChatError, match="SignatureDoesNotMatch"):
        agent_chat.chat([{"role": "user", "content": "hi"}])


def test_chat_bad_responses(monkeypatch):
    """HTTP 400 / 缺 choices / 空 content / 网络失败 → 可读错误。"""
    _enable_api_key(monkeypatch)
    cases = [
        _FakeResp({"odd": 1}, status_code=400),           # 非 200 无业务错误信息
        _FakeResp({"choices": []}),                        # 缺 choices
        _FakeResp({"choices": [{"message": {"content": ""}}]}),   # 空 content
        RuntimeError("connection reset"),                  # 网络失败
    ]
    for item in cases:
        fake = _FakeClient([item])
        monkeypatch.setattr(agent_chat, "_get_client", lambda f=fake: f)
        with pytest.raises(agent_chat.AgentChatError):
            agent_chat.chat([{"role": "user", "content": "hi"}])


# ------------------------------------------------------ extract_markdown 提取消息

def test_extract_markdown_builds_message(monkeypatch):
    """提取请求：多模态 content = 图片 DataUrl + 标识模板指令（不再要求 JSON）；
    按批改类型选模板：默认课时作业模板，考试试卷用试卷模板（含听力部分层级）。"""
    _enable_api_key(monkeypatch)
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return ("## 【大题】Ⅳ 完形填空\n"
                "1. A. cross B. clean C. build D. leave\n"
                "**学生作答：A**\n")

    monkeypatch.setattr(agent_chat, "chat", fake_chat)
    out = agent_chat.extract_markdown("data:image/jpeg;base64,aW1n")
    assert "**学生作答：" in out
    content = captured["messages"][0]["content"]
    assert captured["messages"][0]["role"] == "user"
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,aW1n"
    assert content[1]["type"] == "text"
    assert "作业文本结构化提取" in content[1]["text"]   # 默认课时作业模板
    # 紧凑协议：大题/素材标识 + 题干行下紧跟「**学生作答：**」+ 严禁概括约束
    for tag in ("【大题】", "【素材】", "**学生作答："):
        assert tag in content[1]["text"]
    assert "严禁概括" in content[1]["text"]
    # 考试试卷类型：换试卷模板（听力部分/笔试部分固定层级，听力题跳过）
    agent_chat.extract_markdown("data:image/jpeg;base64,aW1n", "考试试卷")
    exam_text = captured["messages"][0]["content"][1]["text"]
    assert "试卷文本结构化提取" in exam_text
    assert "听力部分" in exam_text and "笔试部分" in exam_text
    assert "听力部分整段跳过" in exam_text


def test_extract_markdown_strips_dataurl_prefix(monkeypatch):
    """裸 base64 容错：DataUrl 头剥掉后拼回标准 DataUrl。"""
    _enable_api_key(monkeypatch)
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return "提取"

    monkeypatch.setattr(agent_chat, "chat", fake_chat)
    agent_chat.extract_markdown("aW1n")
    url = captured["messages"][0]["content"][0]["image_url"]["url"]
    assert url == "data:image/jpeg;base64,aW1n"


def test_extract_markdown_multi_builds_message(monkeypatch):
    """多图联合提取消息：content = 多张图（按页序）+ 跨页合并引导 + 模板指令。"""
    _enable_api_key(monkeypatch)
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return "## 【大题】Ⅳ 完形填空\n2. A. cross\n**学生作答：A**\n"

    monkeypatch.setattr(agent_chat, "chat", fake_chat)
    out = agent_chat.extract_markdown_multi(
        ["data:image/jpeg;base64,aW1n", "aW1n"], "考试试卷")
    assert "**学生作答：" in out
    content = captured["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image_url", "image_url", "text"]
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,aW1n"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,aW1n"  # 裸 base64 容错
    text = content[2]["text"]
    assert "2 张照片" in text                       # 合并引导：按页序关联
    assert "关联起来合并输出" in text
    assert "同一大题只输出一个" in text and "同一题只输出一块" in text
    assert "试卷文本结构化提取" in text            # 模板指令紧随其后


# ---------------------------------------------------------------- _agent_pages 多图联合

def test_agent_pages_multi_join(monkeypatch):
    """多图联合两段式：多张照片一次提取（跨页合并）→ 结构化 → Markdown 透传。"""
    calls = {}

    def fake_multi(images, exam_type=""):
        calls["images"] = list(images)
        calls["exam_type"] = exam_type
        return _md_text()

    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown_multi", fake_multi)
    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: _structured())
    res = photo_check._agent_pages(["b1", "b2"], "课时作业")
    assert calls == {"images": ["b1", "b2"], "exam_type": "课时作业"}
    assert res["markdown"] == _md_text()
    assert len(res["questions"]) == 3
    assert res["questions"][0]["section_full"].startswith("Ⅳ 完形填空")


def test_agent_pages_empty_raises(monkeypatch):
    """多图联合读出 0 题：抛 AgentChatError（调用方降级逐张单图解析）。"""
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown_multi",
                        lambda images, exam_type="": "空白照片")
    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: {"name": "", "questions": []})
    with pytest.raises(agent_chat.AgentChatError, match="多图联合解析未识别到题目"):
        photo_check._agent_pages(["b1", "b2"])


def test_parse_papers_multi_image_joined(monkeypatch):
    """同一学生多张照片：一次 _agent_pages 多图联合（不再逐张单图），合并结果
    来源照片为空 → 提取原文标注「【多页合并】」。"""
    _enable_agent(monkeypatch)
    calls = []

    def fake_pages(images, exam_type=""):
        calls.append(list(images))
        return {"name": "",
                "markdown": "## 【大题】IV 完形填空。…\n2. A. cross\n**学生作答：A**",
                "questions": [{
                    "no": 1, "section": "完形填空",
                    "section_full": "IV 完形填空。",
                    "qtype": "完形填空",
                    "question": "2. Owner ____ the street.",
                    "options": ["A. cross"], "student_answer": "A",
                    "suggested_answer": "A", "passage": "",
                    "grading_marks": [], "confidence": "high",
                    "answer_source": "agent"}]}

    monkeypatch.setattr(photo_check, "_agent_pages", fake_pages)

    def boom(b64, exam_type=""):
        raise AssertionError("多图时应走多图联合，不应逐张单图")
    monkeypatch.setattr(photo_check, "_parse_page", boom)
    report = photo_check.parse_papers([
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p1.jpg", "image_base64": "b1"},
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p2.jpg", "image_base64": "b2"},
    ])
    assert calls == [["b1", "b2"]]                  # 两张一批一次联合
    paper = report["papers"][0]
    assert len(paper["questions"]) == 1
    assert paper["extract_mds"] == [
        "【多页合并】\n## 【大题】IV 完形填空。…\n2. A. cross\n**学生作答：A**"]


def test_parse_papers_multi_falls_back_to_single(monkeypatch):
    """多图联合失败：该批降级逐张单图解析，题目照常产出（不丢照片）。"""
    _enable_agent(monkeypatch)

    def boom_pages(images, exam_type=""):
        raise agent_chat.AgentChatError("多图联合失败")
    monkeypatch.setattr(photo_check, "_agent_pages", boom_pages)
    pages = {
        "b1": {"name": "", "markdown": "md1", "questions": [{
            "no": 1, "section": "完形填空", "section_full": "",
            "qtype": "完形填空", "question": "Owner ____ the street.",
            "options": ["A. cross"], "student_answer": "A", "suggested_answer": "A",
            "passage": "", "grading_marks": [], "confidence": "high",
            "answer_source": "agent"}]},
        "b2": {"name": "", "markdown": "md2", "questions": [{
            "no": 2, "section": "完形填空", "section_full": "",
            "qtype": "完形填空", "question": "He ____ home.",
            "options": ["A. go"], "student_answer": "", "suggested_answer": "A",
            "passage": "", "grading_marks": [], "confidence": "low",
            "answer_source": "agent"}]},
    }
    monkeypatch.setattr(photo_check, "_parse_page", lambda b64, exam_type="": pages[b64])
    report = photo_check.parse_papers([
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p1.jpg", "image_base64": "b1"},
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p2.jpg", "image_base64": "b2"},
    ])
    paper = report["papers"][0]
    assert [q["no"] for q in paper["questions"]] == [1, 2]
    assert paper["extract_mds"] == ["【p1.jpg】\nmd1", "【p2.jpg】\nmd2"]


def test_parse_papers_multi_skips_empty_b64(monkeypatch):
    """批内有照片未读取成功（空 b64）：不参与联合，仍走多图联合其余照片。"""
    _enable_agent(monkeypatch)
    calls = []

    def fake_pages(images, exam_type=""):
        calls.append(list(images))
        return {"name": "", "markdown": "md", "questions": [{
            "no": 1, "section": "完形填空", "section_full": "",
            "qtype": "完形填空", "question": "Owner ____ the street.",
            "options": [], "student_answer": "A", "suggested_answer": "",
            "passage": "", "grading_marks": [], "confidence": "high",
            "answer_source": "agent"}]}

    monkeypatch.setattr(photo_check, "_agent_pages", fake_pages)

    def boom(b64, exam_type=""):
        raise AssertionError("空 b64 不应逐张解析；联合成功后不应逐张单图")
    monkeypatch.setattr(photo_check, "_parse_page", boom)
    report = photo_check.parse_papers([
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p1.jpg", "image_base64": "b1"},
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p2.jpg", "image_base64": ""},
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p3.jpg", "image_base64": "b3"},
    ])
    assert calls == [["b1", "b3"]]                  # 空 b64 被滤出，不影响联合
    paper = report["papers"][0]
    assert len(paper["questions"]) == 1
    assert paper["failed_files"] == ["林一诺/p2.jpg"]  # 未读取成功的照片记入失败清单


# ---------------------------------------------------------------- _agent_page 两段式

def _md_text():
    """智能体紧凑提取协议样例：大题/素材标识 + 题目行下紧跟「**学生作答：**」行。"""
    return ("## 【大题】Ⅳ 完形填空。从各题所给的四个选项中选择最佳答案\n"
            "【素材】\nLoni loved her town where she lived with her grandma…\n"
            "2. A. cross B. clean C. build D. leave\n"
            "**学生作答：A**\n"
            "3. A. improve B. return C. change D. grow\n"
            "**学生作答：未作答**\n")


def _structured():
    """DeepSeek 结构化输出（含需归一化的脏数据：行首题号/题号兑底/自由题型/全空题）。"""
    return {
        "name": "陆思远",
        "questions": [
            {"no": 2, "section": "完形填空",
             "section_full": "Ⅳ 完形填空。从各题所给的四个选项中选择最佳答案",
             "qtype": "完形填空",
             "question": "2. A guide dog was helping its owner ____ the street.",
             "options": ["A. cross", "B. clean", " "], "student_answer": "A",
             "suggested_answer": "A", "passage": ""},
            {"no": "x", "section": "", "qtype": "提示填单词", "question": "",
             "student_answer": "B"},                  # 题号非法 → 兑底；只有作答也保留
            {"no": 3, "section": "完形填空", "section_full": "",
             "qtype": "自创题型",
             "question": "3) I hope our town will never ____.",
             "options": ["A. improve"], "student_answer": "",
             "suggested_answer": "C", "passage": ""},
            {"no": 4, "question": "", "student_answer": ""},   # 全空题过滤
            "bad-string",
        ],
    }


def test_agent_page_builds_questions(monkeypatch):
    """两段式组装：智能体提取 → DeepSeek 结构化 → 题号排序/兑底、题干剥行首
    题号、题型自由转写（不套白名单）、空选项过滤、作答置信度、Markdown 透传。"""
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown",
                        lambda b64, exam_type="": _md_text())
    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: _structured())
    res = photo_check._agent_page("aW1n")
    assert res["name"] == "陆思远"
    assert res["markdown"] == _md_text()               # 提取原文随页透传
    q2, qb, q3 = res["questions"]
    assert q2["no"] == 2 and q2["answer_source"] == "agent"
    assert q2["section"] == "完形填空"                 # 核心名（合并键）
    assert q2["section_full"].startswith("Ⅳ 完形填空")   # 完整标题（展示用）
    assert q2["confidence"] == "high"                  # 有作答 → 直读置信
    assert q2["question"] == "A guide dog was helping its owner ____ the street."
    assert q2["options"] == ["A. cross", "B. clean"]   # 空选项过滤
    assert q2["grading_marks"] == []                   # 批改符号已在提取时过滤
    assert qb["no"] == 2                                # 题号兑底（已有题数+1）
    assert qb["question"] == "" and qb["student_answer"] == "B"
    assert qb["qtype"] == "提示填单词"                  # 题型自由保留（不归「其他」）
    assert q3["no"] == 3 and q3["qtype"] == "自创题型"  # 题型不统一，如实转写
    assert q3["question"] == "I hope our town will never ____."   # 剥 "3) " 行首题号
    assert q3["confidence"] == "low"                   # 未作答
    assert "mark_source" not in q2                     # 不再携带批改API标识


def test_agent_page_empty_raises(monkeypatch):
    """读出 0 题：抛 AgentChatError 由 _parse_page 降级处理（不在此包装）。"""
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown",
                        lambda b64, exam_type="": "空白照片")
    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: {"name": "", "questions": []})
    with pytest.raises(agent_chat.AgentChatError, match="未识别到题目"):
        photo_check._agent_page("aW1n")


def test_md_questions_llm_failure_normalized(monkeypatch):
    """DeepSeek 结构化失败/返回非对象 → 归一为 AgentChatError，与调用失败同走降级。"""
    def boom(messages, **kw):
        raise photo_check.llm.LLMError("DeepSeek 超时")
    monkeypatch.setattr(photo_check.llm, "chat_json", boom)
    with pytest.raises(agent_chat.AgentChatError, match="结构化失败"):
        photo_check._md_questions("任意 Markdown")

    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: ["不是对象"])
    with pytest.raises(agent_chat.AgentChatError, match="非 JSON 对象"):
        photo_check._md_questions("任意 Markdown")


# ---------------------------------------------------------------- _parse_page 主路与降级

def _enable_agent(monkeypatch):
    monkeypatch.setattr(photo_check.config, "PHOTO_PARSE_PROVIDER", "agent")
    monkeypatch.setattr(config, "AGENT_CHAT_BOT_ID", "bot1234567890abcdef")
    monkeypatch.setattr(config, "AGENT_CHAT_API_KEY_SECRET", "k")
    monkeypatch.setattr(config, "AGENT_CHAT_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_SECRET_ACCESS_KEY", "")


def test_parse_page_agent_primary(monkeypatch):
    """已启用 agent 主路：两段式直出题目（不再走 OCR 转录）。"""
    _enable_agent(monkeypatch)
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown",
                        lambda b64, exam_type="": _md_text())
    monkeypatch.setattr(photo_check.llm, "chat_json",
                        lambda messages, **kw: _structured())

    def boom_ocr(b64):
        raise AssertionError("agent 主路成功时不应调用 OCR 转录")
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", boom_ocr)
    parsed = photo_check._parse_page("aW1n")
    assert parsed["name"] == "陆思远"
    assert all(q["answer_source"] == "agent" for q in parsed["questions"])


def test_parse_page_agent_error_falls_back(monkeypatch):
    """agent 失败（如凭据失效）→ 降级 OCR 转录路，题目照常产出。"""
    _enable_agent(monkeypatch)

    def boom(b64, exam_type=""):
        raise agent_chat.AgentChatError("invalid_api_key")
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown", boom)
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", lambda b64: [
        {"channel": "mineru", "text":
         "Q|1|完形填空|Owner ____ the street.|A|A|IV 完形填空\n"}])
    parsed = photo_check._parse_page("aW1n")
    q = parsed["questions"][0]
    assert q["answer_source"] == "transcript" and q["student_answer"] == "A"


def test_parse_page_agent_disabled(monkeypatch):
    """PHOTO_PARSE_PROVIDER=ocr（autouse fixture 已固定）：agent 不被触发。"""
    called = []
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown",
                        lambda b64, exam_type="": called.append(1) or _md_text())
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", lambda b64: [
        {"channel": "mineru", "text": "Q|1|判断题|There is a book. ( )|T|T\n"}])
    parsed = photo_check._parse_page("aW1n")
    assert not called                              # agent 未被触发
    assert parsed["questions"][0]["answer_source"] == "transcript"


def test_parse_page_agent_unconfigured(monkeypatch):
    """选了 agent 但凭据未配置：自动落到 OCR 转录路（旧部署行为不变）。"""
    monkeypatch.setattr(photo_check.config, "PHOTO_PARSE_PROVIDER", "agent")
    monkeypatch.setattr(config, "AGENT_CHAT_BOT_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_API_KEY_SECRET", "")
    monkeypatch.setattr(config, "AGENT_CHAT_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "AGENT_CHAT_SECRET_ACCESS_KEY", "")
    called = []
    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown",
                        lambda b64, exam_type="": called.append(1) or _md_text())
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", lambda b64: [
        {"channel": "mineru", "text": "Q|1|判断题|There is a book. ( )|T|T\n"}])
    parsed = photo_check._parse_page("aW1n")
    assert not called
    assert parsed["questions"][0]["answer_source"] == "transcript"


# ---------------------------------------------------------------- parse_papers 跨照片合并

def test_parse_papers_agent_sections_merge(monkeypatch):
    """多图联合失败降级逐张单图后，agent 路题目仍按大题跨照片合并（无「第N页」
    页隔离）：核心名做合并键（后页没印大题标题也能并上），展示标题取最完整的
    section_full；提取原文透传。"""
    _enable_agent(monkeypatch)
    monkeypatch.setattr(photo_check, "_agent_pages",
                        lambda images, exam_type="": (_ for _ in ()).throw(
                            agent_chat.AgentChatError("多图联合失败（测试桩）")))
    pages = {
        "b1": {"name": "", "markdown": "## 【大题】IV 完形填空。…\n【素材】…", "questions": [{
            "no": 1, "section": "完形填空",
            "section_full": "IV 完形填空。从各题所给的四个选项中选择最佳答案（建议用时:6分钟）",
            "qtype": "完形填空",
            "question": "Owner ____ the street.", "options": ["A. cross"],
            "student_answer": "A", "suggested_answer": "A", "passage": "文章…",
            "grading_marks": [], "confidence": "high", "answer_source": "agent"}]},
        "b2": {"name": "", "markdown": "## 【大题】完形填空（续）\n选项区…", "questions": [{
            "no": 1, "section": "完形填空", "section_full": "",
            "qtype": "完形填空",
            "question": "Owner ____ the street.", "options": [],
            "student_answer": "", "suggested_answer": "A", "passage": "",
            "grading_marks": [], "confidence": "low", "answer_source": "agent"}]},
    }
    monkeypatch.setattr(photo_check, "_parse_page", lambda b64, exam_type="": pages[b64])
    report = photo_check.parse_papers([
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p1.jpg", "image_base64": "b1"},
        {"path": "九年级20班-课时作业-1单元2课时/林一诺/p2.jpg", "image_base64": "b2"},
    ])
    paper = report["papers"][0]
    qs = paper["questions"]
    # 跨照片同大题同号合并为一题（互补），不再按页隔离成两题
    assert len(qs) == 1 and qs[0]["no"] == 1
    assert qs[0]["options"] == ["A. cross"]        # 后页补前页缺的选项
    assert qs[0]["passage"] == "文章…"
    # 展示标题 = 最完整的 section_full（带序号与说明）
    assert qs[0]["section"] == "IV 完形填空。从各题所给的四个选项中选择最佳答案（建议用时:6分钟）"
    assert "hw_pages" not in paper                  # agent 路无标注图
    assert paper["extract_mds"] == ["【p1.jpg】\n## 【大题】IV 完形填空。…\n【素材】…",
                                    "【p2.jpg】\n## 【大题】完形填空（续）\n选项区…"]


def test_section_key_normalization():
    """大题标识归一：去序号/括注/说明文字，剥「部分/大题/试题」后缀——
    智能体自由起名与试卷原印标题落到同一合并键。"""
    assert photo_check._section_key("Ⅳ 完形填空。(建议用时:6分钟,难度:★★)") == "完形填空"
    assert photo_check._section_key("完形填空部分") == "完形填空"
    assert photo_check._section_key("阅读理解大题") == "阅读理解"
    assert photo_check._section_key("Ⅰ 教材回顾 根据3a用短语的正确形式填空") == "教材回顾"
    assert photo_check._section_key("I 教材回顾") == "教材回顾"
    assert photo_check._section_key("") == ""


# ---------------------------------------------------------------- 批改引擎预判直入

def test_assignment_verdict_direct():
    """预判 verdict 直入：correct 满分 / wrong 零分 / blank 空白未答；
    主观题同样直入（AI 桩会把无标记作答判对，直入应保持判错——证明未走 AI）。"""
    report = checking.assignment_check(
        key=[
            {"no": 1, "qtype": "单选", "question": "Q1", "answer": "B", "score": 2},
            {"no": 2, "qtype": "单选", "question": "Q2", "answer": "A", "score": 2},
            {"no": 3, "qtype": "单选", "question": "Q3", "answer": "C", "score": 2},
            {"no": 4, "qtype": "完成句子", "question": "Q4", "answer": "went", "score": 2},
        ],
        answers=[{
            "student_code": "S01", "name": "林一诺", "files": [],
            "items": [
                {"no": 1, "answer": "B", "verdict": "correct"},
                {"no": 2, "answer": "B", "verdict": "wrong"},    # 与参考答案不符
                {"no": 3, "answer": "", "verdict": "blank"},
                {"no": 4, "answer": "go", "verdict": "wrong"},
            ]}],
        unit=None, week=None, class_name="九年级5班")
    items = report["results"][0]["items"]
    assert items[0]["verdict"] == "correct" and items[0]["got"] == 2
    assert items[0]["note"] == "智能批改判定正确"
    assert items[1]["verdict"] == "wrong" and items[1]["got"] == 0
    assert items[1]["note"] == "智能批改判定有误"
    assert items[2]["verdict"] == "wrong" and items[2]["note"] == "空白未答"
    assert items[3]["verdict"] == "wrong" and items[3]["got"] == 0
    assert report["stats"]["wrong_items"] == 3
    assert report["results"][0]["score"] == 25.0        # 2 / (4题×2分) * 100
    assert report["stats"]["inserted_errors"] == 3      # 判错题照常归因入库


def test_assignment_essay_ignores_verdict():
    """作文不走直入：仍由 AI 分维评分（预判 wrong 不影响长文满分）。"""
    report = checking.assignment_check(
        key=[{"no": 1, "qtype": "书面表达", "question": "写一篇介绍家庭的短文",
              "answer": "", "score": 15}],
        answers=[{
            "student_code": "S01", "files": [],
            "items": [{"no": 1, "verdict": "wrong",
                       "answer": "I have a happy family and we do many things together every day."}]}],
        unit=None, week=None, class_name="")
    it = report["results"][0]["items"][0]
    assert it["verdict"] == "correct" and it["got"] == 15
    assert it["rubric"]                                  # 分维评语保留


def test_assignment_without_verdict_unchanged():
    """无 verdict（OCR 转录降级路）：规则比对原样工作（回归）。"""
    report = checking.assignment_check(
        key=[{"no": 1, "qtype": "单选", "question": "Q", "answer": "B", "score": 1},
             {"no": 2, "qtype": "单选", "question": "Q2", "answer": "A", "score": 1}],
        answers=[{"student_code": "S01", "files": [],
                  "items": [{"no": 1, "answer": "B"}, {"no": 2, "answer": "C"}]}],
        unit=None, week=None, class_name="")
    items = report["results"][0]["items"]
    assert items[0]["verdict"] == "correct"
    assert items[1]["verdict"] == "wrong" and items[1]["note"] == "与标准答案不符"


def test_assignment_string_items_still_work():
    """按题号顺序的字符串列表形态（无 verdict 列）不受四元组改造影响。"""
    report = checking.assignment_check(
        key=[{"no": 1, "qtype": "单选", "question": "Q", "answer": "B", "score": 1},
             {"no": 2, "qtype": "单选", "question": "Q2", "answer": "A", "score": 1}],
        answers=[{"student_code": "S01", "items": ["B", "A"]}],
        unit=None, week=None, class_name="")
    assert report["results"][0]["items"][0]["verdict"] == "correct"
    assert report["results"][0]["items"][1]["verdict"] == "correct"
