# -*- coding: utf-8 -*-
"""OCR 转录路防回归（离线桩注入，不依赖网络）。

覆盖点（对应 PHOTO_PARSE_PROVIDER 通道切换的 OCR 侧验收）：
  - ocr_page_multi / ocr_page_dual：单通道路由、单通道失败降级、全失败报错
  - _parse_page：PHOTO_PARSE_PROVIDER=ocr（autouse fixture 固定）纯转录解析；
    agent 主路切换与降级见 test_agent_chat.py

P2 坐标锚点（qwen_vl_ocr advanced_recognition 行坐标）覆盖点：
  - ocr_page_with_boxes：数组 content 的 words_info 解析、字符串 content 降级
  - anchor：题号锚点检测（作答括号变体/非题号行排除）、归一化行匹配、
    带宽内归属与带宽外拒绝
"""
from app.providers import ocr
from app.services import anchor as anchor_mod
from app.services import photo_check


# ---------------------------------------------------------------- 转录构造与 dual 路由

def test_build_raw_single_and_dual():
    single = [{"channel": "mineru", "text": "1. A question"}]
    assert photo_check._build_raw(single) == "1. A question"
    dual = [
        {"channel": "mineru", "text": "A text"},
        {"channel": "paddleocr", "text": "B text"},
    ]
    raw = photo_check._build_raw(dual)
    assert "【转录A·快通道MinerU】" in raw and "【转录B·慢通道PaddleOCR】" in raw
    assert "A text" in raw and "B text" in raw
    assert raw.index("【转录A") < raw.index("【转录B")   # A 主 B 补的阅读顺序


def test_ocr_page_multi_single_channel(monkeypatch):
    """非 dual 文档流：ocr_page_multi 返回单元素列表（photo_check 统一按列表消费）。"""
    monkeypatch.setattr(ocr, "ocr_page", lambda b64: "text")
    monkeypatch.setattr(ocr.config, "OCR_PROVIDER", "mineru")
    monkeypatch.setattr(ocr.config, "DOC_OCR_PROVIDER", "")
    assert ocr.ocr_page_multi("abc") == [{"channel": "mineru", "text": "text"}]


def test_ocr_page_dual_degrades_to_single(monkeypatch):
    """dual 单通道失败降级：MinerU 挂掉时用 PaddleOCR 的转录继续。"""
    def fake_raw(b64, prompt, provider=""):
        if provider == "mineru":
            raise ocr.OCRError("MinerU 链路抖动")
        return "paddle text"
    monkeypatch.setattr(ocr, "_ocr_raw", fake_raw)
    out = ocr.ocr_page_dual("abc")
    assert [t["channel"] for t in out["transcripts"]] == ["paddleocr"]
    assert out["failed"] == ["mineru"]


def test_ocr_page_dual_all_failed(monkeypatch):
    """dual 两通道全失败才抛错（调用方记 failed_files，不阻断其他照片）。"""
    def fake_raw(b64, prompt, provider=""):
        raise ocr.OCRError("down")
    monkeypatch.setattr(ocr, "_ocr_raw", fake_raw)
    try:
        ocr.ocr_page_dual("abc")
        raise AssertionError("两通道全失败应抛 OCRError")
    except ocr.OCRError:
        pass


# ---------------------------------------------------------------- 端到端（离线桩）

def test_parse_page_dual_transcript_merge(monkeypatch):
    """转录降级路端到端：A 为主 B 补漏，重复题不重复输出（桩按 (大题,题号) 去重）。"""
    transcript_a = ("姓名：林一诺\n"
                    "Q|1|完形填空|Great changes ____ in hometown.|have taken place||I 教材回顾\n")
    transcript_b = ("Q|1|完形填空|Great changes ____ in hometown.|||I 教材回顾\n"
                    "Q|2|完形填空|Another two ____ hospitals.|||I 教材回顾\n")
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", lambda b64: [
        {"channel": "mineru", "text": transcript_a},
        {"channel": "paddleocr", "text": transcript_b},
    ])
    parsed = photo_check._parse_page("img64")
    assert parsed["name"] == "林一诺"
    assert [q["no"] for q in parsed["questions"]] == [1, 2]   # B 补的题 2 进来了
    assert parsed["questions"][0]["student_answer"] == "have taken place"  # A 的作答保留
    assert parsed["questions"][1]["question"].startswith("Another two")


def test_parse_page_agent_unconfigured_pure_transcript(monkeypatch):
    """agent 未启用（autouse fixture 已固定 PHOTO_PARSE_PROVIDER=ocr）：纯转录
    路径，agent 不被触发。"""
    def boom(b64, exam_type=""):
        raise AssertionError("agent 未启用时不应被调用")

    monkeypatch.setattr(photo_check.agent_chat, "extract_markdown", boom)
    monkeypatch.setattr(photo_check.ocr, "ocr_page_multi", lambda b64: [
        {"channel": "mineru", "text": "Q|1|完形填空|Question one.|C|A|IV 完形填空\n"}])
    parsed = photo_check._parse_page("img64")
    assert parsed["questions"][0]["question"] == "Question one."


# ---------------------------------------------------------------- P2 坐标锚点

def test_detect_anchors_variants():
    """题号锚点：作答括号变体可命中，大题标题/选项行/挖空正文不误判。"""
    lines = [
        {"text": "IV 完形填空。(建议用时:6分钟)", "box": [100, 20, 800, 30, 0]},
        {"text": "(A) 1. A. cross B. clean C. build D. leave", "box": [100, 60, 800, 24, 0]},
        {"text": "(17)3.A.sandstorms B.mountains", "box": [100, 90, 800, 24, 0]},   # 数字误读括号
        {"text": "8 the way to the center", "box": [100, 120, 800, 24, 0]},          # 挖空行
        {"text": "C. Unless D. Because", "box": [100, 150, 800, 24, 0]},             # 选项行
        {"text": "( ) 10. A. work B. study", "box": [100, 180, 800, 24, 0]},         # 空括号
        {"text": "1. have taken place 2. used to work", "box": [100, 400, 800, 24, 0]},  # 作答区
    ]
    anchors = anchor_mod.detect_anchors(lines)
    assert [a["no"] for a in anchors] == [1, 3, 10, 1]
    # box 高度取的是 box[3]（h），bottom = cy + h/2
    assert anchors[0]["bottom"] == 60 + 24 / 2


def test_find_line_normalized():
    """归一化匹配：空白/标点/大小写差异容忍，取超出量最小的行；未命中 None。"""
    lines = [
        {"text": "1. have taken place .2. used to work", "box": [100, 400, 800, 24, 0]},
        {"text": "(C) 2. A. improve B. return", "box": [100, 90, 800, 24, 0]},
    ]
    assert anchor_mod.find_line("Have Taken Place", lines) is lines[0]
    assert anchor_mod.find_line("used to work", lines) is lines[0]
    assert anchor_mod.find_line("nothing matches", lines) is None


def test_nearest_no_band():
    """带宽归属：锚点下方 1.5 行内命中，超 2.5 倍平均行高拒绝（脱离题区）。"""
    anchors = anchor_mod.detect_anchors([
        {"text": "(A) 1. A. cross", "box": [100, 60, 800, 24, 0]},
    ])
    near = {"text": "x", "box": [100, 100, 200, 24, 0]}    # 锚点 bottom=72，距 28
    far = {"text": "x", "box": [100, 200, 200, 24, 0]}     # 距 128 > 2.5×24
    assert anchor_mod.nearest_no(near, anchors) == 1
    assert anchor_mod.nearest_no(far, anchors) == 0
    assert anchor_mod.nearest_no({"text": "x"}, anchors) == 0   # 无坐标行


def test_ocr_page_with_boxes_parses_words_info(monkeypatch):
    """advanced_recognition 数组 content → text 拼接 + lines 坐标提取。"""
    monkeypatch.setattr(ocr.config, "DASHSCOPE_API_KEY", "sk-test")
    content = [{"type": "text", "text": "(A) 1. A. cross",
                "ocr_result": {"words_info": [
                    {"text": "(A) 1. A. cross", "box": [100, 60, 800, 24, 0]},
                    {"text": "", "box": [1, 2, 3, 4]},            # 空文本行跳过
                    {"text": "bad", "box": [1, 2]},                # 坐标不足跳过
                ]}}]
    monkeypatch.setattr(ocr.llm, "chat_vision_parts", lambda *a, **k: content)
    out = ocr.ocr_page_with_boxes("img64")
    assert out["text"].startswith("(A) 1.")
    assert len(out["lines"]) == 1 and out["lines"][0]["box"] == [100.0, 60.0, 800.0, 24.0]


def test_ocr_page_with_boxes_plain_string(monkeypatch):
    """普通字符串 content（非坐标模式）→ lines 置空安全降级，不报错。"""
    monkeypatch.setattr(ocr.config, "DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(ocr.llm, "chat_vision_parts", lambda *a, **k: "plain text")
    out = ocr.ocr_page_with_boxes("img64")
    assert out["text"].strip() == "plain text" and out["lines"] == []


def test_ocr_page_multi_qwen_carries_lines(monkeypatch):
    """qwen_vl_ocr 文档流：transcript 携带行坐标（锚点匹配的数据源）。"""
    monkeypatch.setattr(ocr.config, "DOC_OCR_PROVIDER", "qwen_vl_ocr")
    monkeypatch.setattr(ocr, "ocr_page_with_boxes",
                        lambda b64: {"text": "t",
                                     "lines": [{"text": "1.", "box": [1, 2, 3, 4]}]})
    out = ocr.ocr_page_multi("abc")
    assert out[0]["channel"] == "qwen_vl_ocr" and out[0]["lines"][0]["box"][0] == 1


# ---------------------------------------------------------------- 原样转录（题干/作答不变形）

def test_prompts_require_verbatim_transcription():
    """三处解析提示词（OCR 转录模板 / Markdown 结构化模板 / 智能体提取×2 类型
    模板）都要求题干/作答原样保留，禁止改写成固定形态；方框选词清单进 passage
    不拼题干；提取提示词带大题/素材标识、题干行下紧跟「**学生作答：**」的紧凑
    协议与严禁概括约束。"""
    from app.providers import agent_chat
    prompts = (photo_check._PARSE_PROMPT_TEMPLATE, photo_check._MD_PARSE_TEMPLATE,
               agent_chat._EXTRACT_PROMPT_LESSON, agent_chat._EXTRACT_PROMPT_EXAM)
    for p in prompts:
        assert "原样" in p
        assert "学生作答" in p
    for p in (photo_check._PARSE_PROMPT_TEMPLATE, photo_check._MD_PARSE_TEMPLATE):
        assert "与原始内容完全一致" in p or "逐字保留" in p
        assert "禁止把空位统一改写" in p          # 空位不得统一成 ____ 固定形态
        assert "不要拼进任何题的 question" in p   # 方框选词清单照原样进 passage
    # 提示词关键结构不变（防误删）：结构化路双字段与输出字段；提取路标识+作答行
    assert "section_full" in photo_check._MD_PARSE_TEMPLATE
    assert "suggested_answer" in photo_check._MD_PARSE_TEMPLATE
    for tag in ("【大题】", "【素材】", "**学生作答："):
        assert tag in agent_chat._EXTRACT_PROMPT_LESSON
        assert tag in agent_chat._EXTRACT_PROMPT_EXAM
    assert "严禁概括" in agent_chat._EXTRACT_PROMPT_LESSON
    assert "严禁概括" in agent_chat._EXTRACT_PROMPT_EXAM
    # 结构化模板识别的输入格式 = 紧凑协议（题目行 + 作答行相邻）
    assert "题目行" in photo_check._MD_PARSE_TEMPLATE
    assert "**学生作答：xxx**" in photo_check._MD_PARSE_TEMPLATE


def test_prompts_split_by_exam_type():
    """两套提取模板按批改类型区分：试卷按「听力部分/笔试部分」固定层级，
    作业按「基础巩固/能力提升」栏目；两版听力题一律跳过（无语音链路）。"""
    from app.providers import agent_chat
    lesson, exam = agent_chat._EXTRACT_PROMPT_LESSON, agent_chat._EXTRACT_PROMPT_EXAM
    assert "课时作业" in lesson and "基础巩固" in lesson and "能力提升" in lesson
    assert "考试试卷" in exam and "听力部分" in exam and "笔试部分" in exam
    assert "单项选择" in exam and "完形填空" in exam and "书面表达" in exam
    for p in (lesson, exam):
        assert "听力题跳过" in p or "听力部分整段跳过" in p or "听力题不在范围内" in p
    assert agent_chat._extract_prompt("考试试卷") is exam
    assert agent_chat._extract_prompt("课时作业") is lesson
    assert agent_chat._extract_prompt("") is lesson   # 未知类型默认作业模板


def test_question_quality_accepts_variable_blank_marks():
    """空位评分放宽：原样转录后空线长度不定，2 条及以上下划线即算含空位句。"""
    assert photo_check._question_quality({"question": "He __ go."}) == 3
    assert photo_check._question_quality({"question": "He ________ go."}) == 3
    assert photo_check._question_quality({"question": "He goes to school."}) == 2
    assert photo_check._question_quality({"question": ""}) == 0
