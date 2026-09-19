"""全部测试共用：临时 SQLite 库 + 离线 LLM 桩。

产品代码只保留真实 API 通道（无 mock 模式）；测试套件改为 monkeypatch 注入
确定性 LLM/OCR 桩——离线可复现，且测试路径与生产路径完全一致（都走
_deepseek_classify / _deepseek_generate，只是数据源被替换）。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_eta.db"
    monkeypatch.setenv("ETA_DB", str(db_file))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from app import db as appdb
    appdb.init_db()
    yield appdb


@pytest.fixture(autouse=True)
def fixed_doc_ocr_provider(monkeypatch):
    """测试固定拍照批改文档流走单通道路由（可打桩的 ocr_page）：不受本机
    .env 的 DOC_OCR_PROVIDER=dual 影响——dual 分支直连真实双 OCR API，
    测试会变慢且无法离线复现；dual 行为由 test_dual_pipeline 显式桩覆盖。
    """
    from app.core import config
    monkeypatch.setattr(config, "DOC_OCR_PROVIDER", "")


@pytest.fixture(autouse=True)
def fixed_photo_parse_provider(monkeypatch):
    """测试固定拍照批改解析走 OCR 转录路（可打桩的 ocr_page_multi）：不受
    本机 .env 的 PHOTO_PARSE_PROVIDER=agent 影响——否则每张桩照片都会
    真实调用智能体会话 API；agent 路径行为由 test_agent_chat 显式开启
    （打桩 agent_chat.parse_page）后覆盖。
    """
    from app.core import config
    monkeypatch.setattr(config, "PHOTO_PARSE_PROVIDER", "ocr")


@pytest.fixture(autouse=True)
def disabled_trace(monkeypatch):
    """测试默认关闭 AI 运行留痕（不在本机 LOG_DIR 落 trace 文件）；
    trace 行为由 test_trace 显式开启（指向 tmp_path）后覆盖。
    """
    from app.core import config
    monkeypatch.setattr(config, "TRACE_ENABLED", False)


# ---------------------------------------------------------------- 确定性 LLM 桩

def _rule_classify(question: str, answer: str, qtype: str):
    """按测试断言所需的最小规则集归因（仅测试基础设施）。

    编号对应新版 36 类本体：A 词汇 / B 语法 / C 语篇 / D 书面表达 / E 答题行为。
    """
    a = (answer or "").strip()
    if not a or a.lower() in ("未作答", "空白", "blank", "n/a"):
        return "E04", 0.95
    if ("阅读" in (qtype or "") or "passage" in (question or "").lower()) \
            and re.search(r"\b(learn|infer)\b", question or "", re.I):
        return "C03", 0.82
    if re.search(r"\bI\s+very\s+(like|love|enjoy)\b", a, re.I):
        return "D04", 0.92
    if re.search(r"\bthere\s+(have|has)\b", a, re.I):
        return "B06", 0.95
    if re.search(r"\b(have|has)\s+\w+(ed|en|ne)?\b[^.!?]{0,50}\b(yesterday|last\s+\w+|ago)\b",
                 a, re.I):
        return "B01", 0.9
    if re.search(r"\b(yesterday|last\s+\w+|ago)\b", question or "", re.I) \
            and re.match(r"^(have|has)\s+\w+", a, re.I):
        return "B01", 0.9   # 跨字段：题干过去时间 + 作答完成体
    if re.search(r"\b(tomorrow|next\s+\w+)\b", question or "", re.I) \
            and re.match(r"^(am|is|are)\s+\w+ing\b", a, re.I):
        return "B01", 0.85  # 跨字段：题干将来时间 + 作答进行体
    return "", 0.3


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    """离线确定性 LLM 桩：归因按信号词返回类别 JSON；切题返回固定题组；练习返回固定题组。"""
    from app.providers import llm as llm_mod

    def chat_json(messages, **kwargs):
        user = messages[-1]["content"] if messages else ""
        # 批改判定（短主观题）：按标记返回固定判定（含错词则判错；含故障则非法返回）
        if "【批改判定】" in user:
            ans = (re.search(r"【学生作答】(.*?)(?:\n【判定标准】|$)", user, re.S)
                   or [None, ""])[1] or ""
            if "故障" in ans:
                return {"correct": "maybe", "reason": "模拟 AI 非法返回（测试桩）"}
            if "错误" in ans or "wrong" in ans.lower():
                return {"correct": False, "reason": "与标准答案意思不符（测试桩）"}
            return {"correct": True, "reason": "意思与标准答案一致（测试桩）"}
        # 作文分维评分：按标记返回固定评分（含"没写完"则扣分）
        if "【作文评分】" in user:
            full = float((re.search(r"【满分】(\d+(?:\.\d+)?)", user) or [None, "15"])[1])
            essay = (re.search(r"【学生作文】(.*?)(?:\n【满分】|$)", user, re.S)
                     or [None, ""])[1] or ""
            if "没写完" in essay or len(essay) < 10:
                return {"score": round(full * 0.4, 1),
                        "dimensions": [{"name": "内容要点", "comment": "要点不全（测试桩）"}],
                        "reason": "要点缺失，未达到要求（测试桩）"}
            return {"score": full, "dimensions": [
                {"name": "内容要点", "comment": "要点齐全（测试桩）"}],
                "reason": "达标（测试桩）"}
        # 拍照读名：从转录文本里提取「姓名：xxx」
        if "【读名任务】" in user:
            transcript = (re.search(r"【转录文本】\n(.*)", user, re.S) or [None, ""])[1] or ""
            m = re.search(r"姓名[:：]\s*(\S+)", transcript)
            name = m.group(1) if m else ""
            content = re.sub(r"姓名[:：]\s*\S+\s*\n?", "", transcript)
            return {"name": name, "content": content.strip()}
        # 拍照批改解析：按转录标记行返回题目（Q|题号|题型|题干|作答|参考答案|大题）
        if "【作业解析】" in user:
            transcript = (re.search(r"【转录文本】\n(.*)", user, re.S) or [None, ""])[1] or ""
            m = re.search(r"姓名[:：]\s*(\S+)", transcript)
            questions = []
            seen = set()  # 双通道两份转录各有 Q 行：A 为主 B 补漏，(大题,题号) 去重
            for ln in transcript.splitlines():
                parts = ln.strip().split("|")
                if len(parts) >= 5 and parts[0] == "Q":
                    key = (parts[6] if len(parts) > 6 else "", parts[1])
                    if key in seen:
                        continue
                    seen.add(key)
                    questions.append({
                        "no": parts[1], "qtype": parts[2], "question": parts[3],
                        "options": [], "student_answer": parts[4],
                        "suggested_answer": parts[5] if len(parts) > 5 else "",
                        "section": parts[6] if len(parts) > 6 else "",
                        "passage": ""})
            return {"name": m.group(1) if m else "", "questions": questions}
        # 学生反馈（导出总结/建议）：返回固定文案
        if "【学生反馈】" in user:
            return {"summary": "存在个别错误（测试桩）", "advice": "针对错题加强练习（测试桩）"}
        # 拍照切题：按【切题任务】标记返回固定题组（一页多题、多题型）
        if "【切题任务】" in user:
            return {"questions": [
                {"qtype": "单选", "question": "He ____ to Beijing twice last year.",
                 "options": ["A. has been", "B. went", "C. has gone", "D. goes"], "passage": ""},
                {"qtype": "判断题", "question": "There is a book on the desk. ( )",
                 "options": [], "passage": ""},
                {"qtype": "阅读理解",
                 "passage": "Tom planted a small tree five years ago. Now it is taller.",
                 "question": "What can we learn from the passage?",
                 "options": ["A. Tom waters it daily.", "B. The tree has grown for five years."]},
                {"qtype": "自创题型应被规范化", "question": "杂题", "options": []},
            ]}
        mq = re.search(r"题目：(.*)\n学生作答：(.*)\n", user)
        question = mq.group(1) if mq else ""
        answer = mq.group(2) if mq else ""
        if answer == "（空白未答）":
            answer = ""
        qt = re.search(r"题型：(.*)\n", user)
        qtype = qt.group(1) if qt else ""
        # 听写错词归因：question 含"听写"时按空白/拼写规则返回
        if "听写" in (question or ""):
            cid, conf = ("E04", 0.95) if not answer.strip() else ("A01", 0.9)
            return {"category_id": cid, "evidence": "听写错词（测试桩归因 %s）" % cid,
                    "teaching_point": "", "confidence": conf}
        cid, conf = _rule_classify(question, answer, qtype)
        if not cid:
            return {"category_id": "", "evidence": "未识别到明确错因信号，请教师判断",
                    "teaching_point": "", "confidence": 0.3}
        return {"category_id": cid, "evidence": "作答命中信号（测试桩归因 %s）" % cid,
                "teaching_point": "", "confidence": conf}

    def chat(messages, **kwargs):
        return json.dumps([
            {"type": "单选", "q": "He ____ to Beijing twice last year.",
             "options": ["has been", "went", "has gone", "goes"],
             "answer": "B", "explanation": "last year 是过去时间，用一般过去时"},
            {"type": "改错", "q": "改正句子：I have seen the film yesterday.",
             "answer": "I saw the film yesterday.",
             "explanation": "yesterday 与一般过去时连用"},
            {"type": "阅读理解",
             "passage": "Tom planted a small tree in front of his house five years ago. "
                        "Now it is taller than the windows.",
             "q": "What can we infer?",
             "options": ["Tom waters it daily.", "The tree has grown for five years."],
             "answer": "B", "explanation": "基于文本、止步文本"},
        ], ensure_ascii=False)

    monkeypatch.setattr(llm_mod, "chat_json", chat_json)
    monkeypatch.setattr(llm_mod, "chat", chat)
