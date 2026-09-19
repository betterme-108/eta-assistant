"""拍照批改解析：上传批次文件夹照片 → 按学生分组 → 智能体会话主路（OCR转录降级）。

批改组的唯一入口（课时作业批改 / 考试试卷批改共用）：
  老师把整个批次文件夹（班级-批改类型-唯一标识 或 两段式 班级-唯一标识，
  下为学生姓名/照片）一次上传，不需要手抄答案与作答——
  ① uploads.organize 按命名分组（一名学生一个文件夹，多张照片；两段式
    批次根的批改类型取当前批改页传入的 exam_type）；
  ② 智能体会话主路（PHOTO_PARSE_PROVIDER=agent 且 AGENT_CHAT_* 已配置，
    火山联网问答Agent，两段式）：同一学生多张照片优先多图联合提取——
    相邻几张（≤_AGENT_BATCH）一次交给智能体，把跨页的题干/选项/作答
    关联起来合并输出（某大题的题干在一页、选项或作答在另一页是常态，
    逐张单图会漏内容）；智能体把照片提取成 Markdown（大题完整标题/原文
    素材/题干/学生作答标注），DeepSeek 再把 Markdown 结构化并顺带给出
    参考答案（confidence=high；图像理解归智能体，文本结构化归 LLM）；
    多图失败降级逐张单图——课时作业与考试试卷各按自己的解析模板提取
    （试卷固定「听力部分/笔试部分」层级，作业按「基础巩固/能力提升」栏目；
    听力等音频类题目一律跳过，本系统无语音链路）；
  ③ OCR转录降级路（agent 未配置/失败时）：DOC_OCR_PROVIDER；
    dual 时 mineru + paddleocr 双转录互补，融合由解析 LLM 完成；
  ④ 跨照片合并（同大题同题号互补，多图联合批与批之间同样适用），
    大题内重号按首次出现顺序全局重编号；
  ⑤ 学生姓名优先取文件夹名（比 OCR 更可靠），与名册匹配供老师确认/改选；
  ⑥ 老师在前端逐题核对/修正后交给 checking.assignment_check 走既有批改引擎
    （客观题规则判卷 / 主观题 AI 判定 / 作文分维评分），
    每题携带来源照片（file），错题据此关联到具体上传文件。

反标签化：姓名只用于本机名册匹配（复用 papers.match_roster），
不进入任何错题记录；送入大模型的只有照片转录文本与图像本身（题目与作答）。
"""
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from ..core import config, trace
from ..providers import agent_chat, llm, ocr
from . import uploads, options
from .papers import match_roster

logger = logging.getLogger("eta.services.photo_check")

# agent 多图联合解析的批大小：一次最多把几张照片一起交给智能体（跨页关联）。
# 超过分批处理，批与批之间由 parse_papers 的跨照片合并兜底；批量太大会拖慢
# 请求、增大失败风险（失败时整批降级逐张单图）。
_AGENT_BATCH = 4

_QTYPES = ("单选", "多选", "判断题", "语法选择", "完形填空", "阅读理解", "口语应用",
           "任务型阅读", "完成句子", "概要补全", "书面表达", "词语运用", "听写", "其他")

# 解析指令（OCR 转录 → 题目 JSON）：批改类型注入照片形态描述（{photo_desc}），
# 两种类型各按自己的模板约束——课时作业（课本/作业书形态）与考试试卷
# （听力/笔试整卷形态）；听力题两版均跳过（本系统无语音链路，不做音频题）。
_PARSE_PROMPT_TEMPLATE = """【作业解析】下面是一张学生英语作业照片的转录文本（OCR 原样转录，可能残留版面噪声；
双通道时含【转录A·快通道MinerU】与【转录B·慢通道PaddleOCR】两份转录，内容互为补充）。
{photo_desc}
【任务】首先把每道题的印刷题干与该生手写作答逐字转录出来，按大题整理输出 JSON。
题干与学生作答都必须与原始内容完全一致：不修改、不变形、不增删、不翻译、不规范化——
印刷的空线/空位按原样保留（原有多少条横线就抄多少条；句中夹着的空格编号数字照抄原样，
如 "vegetables 3 to sell" 的 3），禁止把空位统一改写成 ____ 之类的固定形态。
题干只含印刷内容，手写内容只进 student_answer，两者严格分离，互不混入。
【规则】
1. section：该题所属大题的标识 = 序号 + 标题核心词，如 "I 教材回顾"、"IV 完形填空"、
   "V 阅读理解"（去掉括注（建议用时/难度）与说明文字）；同一大题的题必须一致。
   照片上看不到大题标题时按题目形态推断标题（不带序号）：约十道小题、每题 A-D 四个
   选项且共用一篇短文 → "完形填空"；有文章材料的题 → "阅读理解"；实在无法判断输出 ""
2. questions 按大题内题号输出，每题字段：
   - no：大题内题号（整数；没有明显题号按出现顺序 1,2,3…）
   - qtype：{qtypes}；听力题不在范围内，跳过不输出。选词/选短语填空、按提示填单词用
     "词语运用"；按提示改写句子/连词成句用 "完成句子"；完形填空大题的每小题用 "完形填空"
   - question：题干原文逐字转录，只含印刷内容：印刷的空线/空位按原样保留（原有多少条
     横线就抄多少条，禁止增减或统一成固定写法）；句中的括号提示（如 (great)/(产品)）
     保留在原位；句中夹着的空格编号数字照抄原样（如 "owner 1 the street" 的 1）；
     学生手写填入的词不属于题干（识别要点见规则 5）。
     完形填空的 question 是空格所在句（空位按原样），禁止把选项串（A. … B. …）当题干；
     选项只能进 options
   - options：选择题选项逐字抄录（保留 A. B. C. D. 字母前缀）；非选择题输出空数组
   - student_answer：该生此题手写作答逐字抄录（未作答输出空字符串；保留大小写与标点，
     有涂改只取最终结果；识别要点见规则 5）
   - suggested_answer：你根据题目本身给出的正确答案（客观题给标准答案如 "A"；填空给词或
     短语；主观题给参考要点；不确定也给最可能的）
   - passage：完形填空/阅读理解的文章原文，只放到该大题 no=1 的题，其余留空字符串
3. 双通道择优：A、B 两份转录可能有差异，同一题按以下顺序选更好的那份：
   ① 保留空格编号（句子中夹着的数字，如 "vegetables 3 to sell"）的优于丢失的；
   ② 句子文字更完整、更通顺的优于残缺的（如丢词、断句错乱）；
   ③ 两份都完整时以 A 为准。选中哪份就逐字用哪份，不得改写。
   例：A 转录 "they grew a few vegetables to sell at the local market"（丢空号），
   B 转录 "they grew a few vegetables 3 to sell at the local market"（空号 3），
   该句取 B，空号 3 原样保留在 question 里。
4. 完形填空跨页：书本大题常跨两张照片——一张只有文章（含空格编号），另一张是选项。
   只要文章段落里夹着空格编号（如 "owner 1 the street"、"said 7"），这就是完形填空，
   每个空格编号都是一道小题（空格编号 = 小题号），无论本页有没有选项都必须全部输出：
   - 本页有文章句子：按空格编号输出小题（no=空格编号，question=空格所在句，逐字转录，
     空位与编号均按原样；options 有则填，没有输出空数组，由另一张照片互补）。
     同一句含多个空格编号时按子句拆分到各题（如 "6 many things changed…" 与
     "Grandma said 7" 同句：第 6 题 question 写到 friendly people 为止，
     第 7 题 question 是 "Grandma said" 所在子句，空位照印刷原样），不要整句重复给每题；
   - 本页只有选项：选项按题号输出；question 只写本页文章中该空格编号对应的句子，
     本页找不到对应句输出空字符串——禁止把文章其他句子或段落当成该题题干。
     选项前后出现的文章段落（即使只是段落残句）也属于该大题：句中夹着的数字就是
     空格编号（如 "8 the way to the center" 的 8 是第 8 小题），该句必须写入对应
     题号（no=8）的 question，空位按原样；
   - passage 完整输出本页文章（空格编号保留原样），跨页由系统合并
5. 学生作答识别要点：
   - 选择题：题号前括号里的字母是学生作答，如 "(C) 2."、"1.(A)"；空括号 "( )" 表示未作答。
     OCR 常把手写字母误读成数字（如 B→17、A→7、D→1）：括号内是 1-2 位纯数字且该题有
     选项时，结合上下文与选项内容还原成对应字母；还原不了输出空字符串。
     作答也可能写在题目行尾或选项行尾（如 "6. A. If B. Although C. Unless D. Because B"）
   - 填空题：空格位置多出来的词、或紧跟在括号提示前的词，通常是学生手写填入的作答，
     如转录 "another two high-tech(高科技的) hospitals" → student_answer 为 high-tech，
     question 里该位置按转录中可见的印刷空线原样保留；转录看不到空线形态时写 ____ 占位。
     先分清「印刷已给」与「学生补写」：印刷体印出的字母前缀（如 incom___、v__getables
     已印的部分）、括号提示词（如 (great)/(be)）与空线前后的印刷文字属于题干，照原样
     保留在 question，不进作答；只有学生手写补上的字母/单词/短语才进 student_answer
     ——作答只写补写的部分本身（哪怕只有一两个字母），学生把整个词重写了一遍时
     作答才写整个词
   - 单独成行的编号作答（如 "1. have taken place 2. used to work"）是学生作答区，按题号
     配到 student_answer，不要并入题干
   - 对话/情景题：题目引号内学生填写的词句是作答，进 student_answer；引号内该空位按
     印刷原样保留在题干，不得改写成固定写法；引号外的对话上下文（印刷陈述句）是题干，
     逐字保留
   - 按提示完成句子/连词成句类：➢ ▶ 等符号引出的提示词与左栏对话是题干；学生写的完整
     句子是作答
6. 双栏排版：书本版面可能左右两栏（左栏对话/提示、右栏作答），转录顺序可能交错，请按
   题号与语义重新配对，不要按出现顺序机械照抄
7. 方框选词/选短语题：候选词/短语清单（表格或并排罗列）是该大题的印刷素材，照原样完整
   抄录（保留原顺序与分隔，不加任何包装文字），放到该大题 no=1 的 passage 字段；
   不要拼进任何题的 question
8. 忽略页眉页脚、页码、栏目名（基础巩固/能力提升）、得分栏、装订线与批改符号（√ × ✓
   圈画划线）；大题标题与说明（如 "完形填空。（建议用时:6分钟，难度:★★）"）不进
   question，只用于确定 section
9. 只按转录文本整理，不得改写、翻译或补全题目；题干与学生作答都必须与原始内容完全
   一致；无法辨认的部分原样保留；除规则 4 允许
   的情况外 question 不得为空（完形小题找不到空格所在句可写文章该段原文）
【转录文本】
{raw}
【输出约束】只输出一个 JSON：
{{"name": "学生姓名或空字符串", "questions": [{{"no": 1, "section": "完形填空", "qtype": "完形填空", "question": "题干", "options": [], "student_answer": "作答", "suggested_answer": "参考答案", "passage": ""}}]}}
照片上没有任何题目时输出 {{"name": "", "questions": []}}"""


# 照片形态描述（按批改类型注入模板）：课时作业书与考试试卷形态不同，
# 解析时的识别要点不同——试卷先定位笔试部分再逐大题，作业按书本大题形态。
_PHOTO_DESC_LESSON = (
    "照片多拍自课本或课时作业书：印刷题干（含大题标题、方框选词、双栏对话）与学生手写作答"
    "（写在题前括号里、填在空格上、或写在题目下方作答区）混在一起，可能还有未滤净的批改符号。")
_PHOTO_DESC_EXAM = (
    "照片是考试试卷：听力部分与笔试部分的大题（单项选择/完形填空/阅读理解/词汇运用/"
    "书面表达）与学生手写作答混在一起；听力题不在范围内，跳过不输出，只解析笔试部分。")


def _parse_prompt(exam_type: str, raw: str) -> str:
    """OCR 转录降级路解析指令：按批改类型注入照片形态（试卷/作业两套模板）。"""
    is_exam = (exam_type or "").strip() == "考试试卷"
    return _PARSE_PROMPT_TEMPLATE.format(
        qtypes="/".join(_QTYPES), raw=raw,
        photo_desc=_PHOTO_DESC_EXAM if is_exam else _PHOTO_DESC_LESSON)


# 双转录通道标记（dual 文档流）：与 prompt 里的互补合并说明对应
_DUAL_MARKS = {"mineru": "【转录A·快通道MinerU】", "paddleocr": "【转录B·慢通道PaddleOCR】"}


def _build_raw(transcripts: List[Dict[str, str]]) -> str:
    """转录列表 → prompt 原文：单通道直接用文本，双通道带标记拼接。"""
    if not transcripts:
        return ""
    if len(transcripts) == 1:
        return transcripts[0].get("text") or ""
    parts = []
    for i, t in enumerate(transcripts):
        mark = _DUAL_MARKS.get(t.get("channel") or "", "【转录%s】" % chr(65 + i))
        parts.append("%s\n%s" % (mark, (t.get("text") or "").strip()))
    return "\n\n".join(parts)


def _parse_page(image_base64: str, exam_type: str = "") -> Dict[str, Any]:
    """一张照片 → {name, questions}：主路/降级路由 + 运行留痕。

    实际解析在 _parse_page_routes（智能体会话主路，OCR转录降级）；
    exam_type 决定两套解析模板（课时作业/考试试卷）哪套生效。本包装把
    输入规模、实际路由与产出题目落 trace，便于复盘每张照片的解析效果
    （route 取首题 answer_source：agent/transcript）。
    """
    t0 = time.time()
    brief = {"image_base64_len": len(image_base64 or "")}
    try:
        page = _parse_page_routes(image_base64, exam_type)
    except ocr.OCRError as exc:
        trace.record("photo_parse", input=brief, error=exc,
                     meta={"route": "failed", "exam_type": exam_type,
                           "latency_ms": int((time.time() - t0) * 1000)})
        raise
    qs = page.get("questions") or []
    route = (qs[0].get("answer_source") if qs else "") or "empty"
    trace.record("photo_parse", input=brief, output=page,
                 meta={"route": route, "exam_type": exam_type,
                       "latency_ms": int((time.time() - t0) * 1000)})
    return page


def _parse_page_routes(image_base64: str, exam_type: str = "") -> Dict[str, Any]:
    """一张照片 → {name, questions}：智能体会话主路，OCR转录降级。

    ① agent 主路（PHOTO_PARSE_PROVIDER=agent 且凭据已配置）：多模态照片
       直读，整页结构化解析（题干/选项/原文/作答/参考答案一次产出）；
    ② agent 失败/未配置/读出 0 题 → OCR转录路：OCR 转录 → LLM 逐张解析。
    两条路都按 exam_type 套用对应类型的解析模板。
    全部失败抛 OCRError（调用方记 failed_files，不阻断其他照片）。
    """
    if image_base64 and config.use_agent_parse():
        try:
            return _agent_page(image_base64, exam_type)
        except agent_chat.AgentChatError as exc:
            logger.warning("智能体会话解析失败，降级 OCR 转录解析：%s", exc)
    transcripts = ocr.ocr_page_multi(image_base64)
    if not transcripts or not (transcripts[0].get("text") or "").strip():
        raise ocr.OCRError("照片未识别到文字")
    raw = _build_raw(transcripts)
    messages = [
        {"role": "system",
         "content": "你是学生作业解析引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user",
         "content": _parse_prompt(exam_type, raw[:12000])},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.1)
    except llm.LLMError as exc:
        logger.warning("作业解析 AI 失败：%s", exc)
        return {"name": "", "questions": []}
    if not isinstance(data, dict):
        return {"name": "", "questions": []}
    out: List[Dict[str, Any]] = []
    for it in (data.get("questions") or []):
        if not isinstance(it, dict):
            continue
        question = str(it.get("question") or "").strip()
        if not question:
            continue
        qtype = str(it.get("qtype") or "").strip()
        if qtype not in _QTYPES:
            qtype = "其他"
        section = str(it.get("section") or "").strip()[:20]
        # 选项异常检查：重复字母（两个A）、内容重复、字母缺失/错序 → 规范重编号
        norm_opts, opt_issues = options.normalize_options(it.get("options"))
        try:
            no = int(it.get("no"))
        except (TypeError, ValueError):
            no = len(out) + 1
        out.append({
            "no": no,
            "section": section,
            "qtype": qtype,
            "question": question,
            "options": norm_opts,
            "student_answer": str(it.get("student_answer") or "").strip(),
            "suggested_answer": str(it.get("suggested_answer") or "").strip(),
            "passage": str(it.get("passage") or "").strip(),
            "grading_marks": [],      # 老师批改符号（agent 解析识别；批改校验备用）
            "confidence": "low",      # 作答来源置信度：high=agent 直读 / low=转录猜测
            "answer_source": "transcript",
        })
        if opt_issues:
            out[-1]["option_issues"] = opt_issues
    out.sort(key=lambda q: q["no"])
    return {"name": str(data.get("name") or "").strip()[:20], "questions": out}


def _agent_page(image_base64: str, exam_type: str = "") -> Dict[str, Any]:
    """智能体会话主路（两段式）：图像提取 → DeepSeek 结构化 → 标准 {name, questions}。

    ① agent_chat.extract_markdown：智能体按 web 端同款方式输出 Markdown
       （大题完整标题 / 原文素材 / 题干 / 学生作答标注），exam_type 决定
       提取模板（课时作业 / 考试试卷）；
    ② _md_questions：DeepSeek 把 Markdown 整理成题目 JSON（顺带给出参考答案）。
    读出 0 题抛 AgentChatError（调用方降级 OCR 转录路）。
    """
    md = agent_chat.extract_markdown(image_base64, exam_type)
    page = _md_questions(md, exam_type)
    if not page["questions"]:
        raise agent_chat.AgentChatError("智能体会话解析未识别到题目")
    page["questions"].sort(key=lambda q: q["no"])
    # Markdown 原文随页透传（前端「智能体提取原文」视图直显，对齐 web 端效果）
    page["markdown"] = md
    return page


def _agent_pages(images: List[str], exam_type: str = "") -> Dict[str, Any]:
    """智能体会话主路（多图联合）：多张照片一次提取 → DeepSeek 结构化。

    学生照片常跨页——某大题的题干在一页、选项或学生作答在另一页，同一题
    也可能跨页续排，逐张单图解析会漏内容；多图联合让智能体把多页内容
    关联起来合并输出（同一大题一个标题、同一题一块），DeepSeek 再结构化。
    读出 0 题或任何一步失败抛 AgentChatError（调用方降级逐张单图解析）。
    """
    md = agent_chat.extract_markdown_multi(images, exam_type)
    page = _md_questions(md, exam_type)
    if not page["questions"]:
        raise agent_chat.AgentChatError("多图联合解析未识别到题目")
    page["questions"].sort(key=lambda q: q["no"])
    page["markdown"] = md
    return page


# Markdown → 题目 JSON 的结构化指令（DeepSeek）：识别智能体紧凑提取协议
# （## 【大题】/【素材】标识 + 题目行下紧跟「**学生作答：**」行）；大题名拆
# 双字段——section 核心名做跨照片合并键、section_full 完整标题做展示；题型
# 不统一是常态：qtype 自由转写不强套白名单，仅保留批改所需的最小结构。
# {kind} 按批改类型注入（课时作业/考试试卷）；听力题一律跳过（无语音链路）。
_MD_PARSE_TEMPLATE = """【作业结构化】下面是智能体对学生英语{kind}照片的 Markdown 提取结果，格式紧凑：
每个大题由「## 【大题】」标题行起头，可选「【素材】」段（文章原文/选词清单）；
其后逐题排列——题目行（题号 + 题干；选择题选项紧跟下一行）下紧跟一行
「**学生作答：xxx**」；大题内也可能有集中作答块「**学生填写答案：**」+ 编号列表。
请把它整理成 JSON。要求：
1. name：照片上的学生姓名（提取结果里没有则空字符串）
2. questions：每道小题一个对象，字段：
   - no：大题内题号（整数；题号在题目行行首，语篇题的文章空格编号就是题号）
   - section：所属大题的核心名，全卷统一用标准短名（如 教材回顾/完形填空/
     阅读理解/提示填单词/完成句子/书面表达；取完整标题的名称部分，去掉序号、
     建议用时等括注与题目说明，去掉「类大题/部分」等修饰）——同名大题在两张
     照片上才能合并
   - section_full：该大题的完整标题（## 【大题】后照原样，含序号+名称+题目说明）；
     提取结果里没有完整标题时输出空字符串
   - qtype：按试卷标注的题型原样输出（题型不统一，如实转写，如 单选/完形填空/
     阅读理解/选词填空/提示填单词/完成句子/书面表达…）；试卷未标注的按题目形态
     概括（选词/选短语填空用“词语运用”；连词成句/按要求改写句子用“完成句子”）
   - question：题目行的原文（只含印刷内容，不含题号；空线/空位/括号提示/句中
     夹着的空格编号按原样保留，禁止把空位统一改写成 ____；选择题题干不含选项；
     连词成句/按要求改写这类题，题干写提示词与题目要求；同一句含多个空格编号
     时按子句拆分到各题，第 N 题的题干只写到第 N 个空位所在子句，不要整句
     重复给每题）
   - options：选择题选项数组（保留 A. B. C. D. 前缀）；非选择题空数组
   - student_answer：紧跟该题题目行的「**学生作答：xxx**」内容（未作答 → 空字符串）；
     集中作答块「**学生填写答案：**」下的编号条目按题号配到对应题；
     作答只含学生手写补写的部分——印刷体已给的字母前缀/括号提示词/空线属于题干，
     照提取原样留在 question，不并入作答
   - suggested_answer：你根据题目与【素材】给出的正确答案（客观题给字母如 "A"；
     填空给词或短语；主观题给参考要点；不确定也给最可能的）
   - passage：该大题【素材】的文章原文或选词/选短语清单，只放到该大题第 1 题，
     其余空字符串；不要拼进任何题的 question
3. 语篇题（完形填空/短文选词填空）：【素材】文章里夹着的空格编号就是小题号，
   每个编号一道小题（question 为空格所在的句子），选项来自该题的选项行
4. 跨页归并：提取结果若同一大题出现多个标题段、或同一题号出现多块（题干/选项/
   作答可能分在不同页或不同段）：合并为一块——question 取更完整的一份，
   options/学生作答/suggested_answer/passage 取非空的，不要重复输出同一题，
   也不要漏掉只出现在其中一段的题目或作答
5. 逐字保留提取结果里的题目内容，不得改写、翻译或补全；听力题跳过；
   试卷开头的试卷标题行（# 开头）忽略，不计入题目
【提取结果】
{md}
【输出约束】只输出一个 JSON：
{{"name": "", "questions": [{{"no": 1, "section": "", "section_full": "", "qtype": "", "question": "", "options": [], "student_answer": "", "suggested_answer": "", "passage": ""}}]}}
没有任何题目时输出 {{"name": "", "questions": []}}"""


def _md_parse_prompt(exam_type: str, md: str) -> str:
    """Markdown 结构化指令：按批改类型注入照片类型描述（试卷/作业两套模板）。"""
    kind = "考试试卷" if (exam_type or "").strip() == "考试试卷" else "课时作业"
    return _MD_PARSE_TEMPLATE.format(kind=kind, md=md)


# 题干行首的题号（"1. ____ changed…" / "2) ____"）——DeepSeek 偶尔没剥，统一后端剥
_LEADING_NO_RE = re.compile(r"^\s*\d{1,3}\s*[.、．)）]\s*")


def _md_questions(md: str, exam_type: str = "") -> Dict[str, Any]:
    """Markdown 提取文本 → 归一化 {name, questions}（DeepSeek 结构化）。

    结构化失败（LLM 错误/非 JSON 对象）归一为 AgentChatError，与调用失败
    同走降级；题型自由转写不强套白名单（题型不统一）；作答为智能体直读
    （confidence=high），批改符号已在提取时过滤（grading_marks 恒空）。
    """
    messages = [
        {"role": "system",
         "content": "你是学生作业结构化引擎，只输出一个 JSON 对象，不输出任何其他文字。"},
        {"role": "user",
         "content": _md_parse_prompt(exam_type, (md or "")[:16000])},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.1)
    except llm.LLMError as exc:
        logger.warning("Markdown 结构化失败：%s", exc)
        raise agent_chat.AgentChatError("智能体会话提取结果结构化失败") from exc
    if not isinstance(data, dict):
        raise agent_chat.AgentChatError("智能体会话提取结果结构化异常（非 JSON 对象）")
    questions: List[Dict[str, Any]] = []
    for vq in (data.get("questions") or []):
        if not isinstance(vq, dict):
            continue
        question = str(vq.get("question") or "").strip()
        answer = str(vq.get("student_answer") or "").strip()
        if not question and not answer:
            continue
        qtype = str(vq.get("qtype") or "").strip()
        # 选项异常检查：重复字母（两个A）、内容重复、字母缺失/错序 → 规范重编号
        norm_opts, opt_issues = options.normalize_options(vq.get("options"))
        try:
            no = int(vq.get("no"))
        except (TypeError, ValueError):
            no = len(questions) + 1
        questions.append({
            "no": no,
            "section": str(vq.get("section") or "").strip()[:20],
            "section_full": str(vq.get("section_full") or "").strip()[:60],
            "qtype": qtype[:20],                    # 题型自由转写（不套白名单）
            "question": _LEADING_NO_RE.sub("", question),   # 剥行首题号
            "options": norm_opts,
            "student_answer": answer,
            "suggested_answer": str(vq.get("suggested_answer") or "").strip(),
            "passage": str(vq.get("passage") or "").strip(),
            "grading_marks": [],      # 批改符号已在智能体提取时过滤
            "confidence": "high" if answer else "low",   # 作答为智能体直读
            "answer_source": "agent",
        })
        if opt_issues:
            questions[-1]["option_issues"] = opt_issues
    return {"name": str(data.get("name") or "").strip()[:20],
            "questions": questions}


_SECTION_ROMAN_PREFIX_RE = re.compile(
    r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}|[IVXivx]{1,4})(?=[\u4e00-\u9fff、.．:：\s]|$)[、.．:：\s]*")
_SECTION_CN_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]{1,3}(?=[\u4e00-\u9fff、.．:：\s]|$)[、.．:：\s]*")
_SECTION_PAREN_NOTE_RE = re.compile(r"[（(][^（）()]*?(?:建议用时|难度)[^（）()]*?[）)]")

_ROMAN_VALUES = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
                 "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_CN_VALUES = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SECTION_ORDINAL_RE = re.compile(
    r"^([IVXivx]{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}|[一二三四五六七八九十]{1,3})"
    r"(?=[\u4e00-\u9fff、.．:：\s]|$)")


def _section_ordinal(section: str):
    """大题序号（I/Ⅱ/一… → 1-10），无序号返回 None（排序用）。

    序号后必须跟中文/分隔符，避免把 Vocabulary 的 V 当序号。
    """
    s = (section or "").strip()
    if not s:
        return None
    m = _SECTION_ORDINAL_RE.match(s)
    if not m:
        return None
    token = m.group(1)
    if token in _CN_VALUES:
        return _CN_VALUES[token]
    upper = token.upper()
    if upper in _ROMAN_VALUES:
        return _ROMAN_VALUES[upper]
    return {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
            "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10}.get(token)


def _section_key(section: str) -> str:
    """大题标识归一：去序号/括注/说明文字，取标题核心词，跨照片同大题才能合并。

    书本大题常跨两张照片：上一张带大题标题（如 "IV 完形填空。(建议用时:6分钟)"），
    下一张直接是题目；agent 路的标题是完整的（含题目说明，如 "Ⅰ 教材回顾
    根据3a用短语的正确形式填空"），归一取名称核心词（首个词段，不含说明）后
    两侧才能落到同一 key。序号后必须跟中文/分隔符才剥离，避免误伤
    Vocabulary 这类英文标题首字母。
    """
    raw = (section or "").strip()
    s = _SECTION_PAREN_NOTE_RE.sub("", raw)
    s = _SECTION_ROMAN_PREFIX_RE.sub("", s)
    s = _SECTION_CN_PREFIX_RE.sub("", s)
    s = re.split(r"[\s。．.、:：,，;；]", s, 1)[0] if s else ""
    s = re.sub(r"(部分|大题|试题)$", "", s)     # "完形填空部分" → "完形填空"
    s = re.sub(r"[\s。.．、:：·—\-◆]+", "", s)
    return s[:12] or raw[:12]


def _question_quality(q: Dict[str, Any]) -> int:
    """题干质量评分（合并择优用）：含空位的句子 > 普通句子 > 选项串 > 空。

    选项页常先于文章页处理（文件名自然序），若后页 LLM 违规把选项串
    填进 question，评分低于文章页的含空句子，合并时会被正确题干替换。
    """
    t = str(q.get("question") or "").strip()
    if not t:
        return 0
    if re.search(r"_{2,}", t):
        return 3   # 含空位占位（原样转录后横线长度不定，2 条及以上即算）
    if re.match(r"^A[.、．]\s", t) and q.get("options"):
        return 1   # 选项串（LLM 违规填入题干）
    return 2


def parse_papers(items: List[Dict[str, Any]], exam_type: str = "",
                 class_name: str = "") -> Dict[str, Any]:
    """整份批次照片 → 按学生分组解析：智能体会话主路（OCR转录降级）→ 名册匹配。

    items: [{path: 批次根/学生姓名/文件名, image_base64}]，分组与页序由
    uploads.organize 统一裁定（文件名自然排序即页序）；exam_type 为当前
    批改页的批改类型（两段式批次根命名时作为批改类型来源）；class_name 为
    批改页右上角所选班级——名册匹配只在该班学生里找（未选班级则全量匹配）。
    一名学生多张照片：同大题同题号跨照片合并（题干/选项/原文/参考答案字段互补——
    完形原文在上一张、选项在下一张是常态；作答取首个非空）；
    学生姓名优先取文件夹名，名册未匹配时再用视觉/OCR 读出的姓名试一次；
    每题携带来源照片（file = 相对批次根的学生/文件名）。
    单张识别失败不阻断其余照片；返回 {meta, warnings, papers: [...]}。
    """
    plan = uploads.organize([str(it.get("path") or "") for it in items], exam_type)
    root = plan["meta"]["folder"]
    # 批改类型以 organize 解析为准（三段式文件夹名权威，两段式取页面传入），
    # 决定每张照片走哪套解析模板（课时作业 / 考试试卷）
    exam = (plan["meta"].get("exam_type") or "").strip() or (exam_type or "").strip()
    b64_by_path = {uploads.clean_path(it.get("path")): it.get("image_base64") or ""
                   for it in items}

    papers: List[Dict[str, Any]] = []
    for idx, group in enumerate(plan["groups"]):
        student = group["name"]
        entry: Dict[str, Any] = {
            "index": idx + 1, "name": student, "name_source": "folder",
            "student_code": "", "matched": False, "name_local": "",
            "ocr_error": "", "failed_files": [], "files": group["files"],
            "questions": [],
        }
        entry.update(match_roster(student, class_name))
        merged: Dict[Tuple[str, int], Dict[str, Any]] = {}
        section_ord: Dict[str, int] = {}   # 大题 key → 序号（任一题见到带序号标题即记下）
        canonical: Dict[str, str] = {}     # 大题 key → 带序号的规范标题（统一显示用）
        ocr_names: List[str] = []
        extract_mds: List[str] = []        # 智能体提取原文（每段一张/一组照片，前端直显）
        b64s = [ocr.normalize_image(b64_by_path.get(root + "/" + rel, ""))
                for rel in group["files"]]
        # 多图联合优先：相邻多张照片一次交给智能体，把跨页的题干/选项/作答
        # 关联起来合并输出（某大题的题干在一页、选项或作答在另一页是常态，
        # 逐张单图会漏内容）；单张批仍走单图路以保留来源照片；失败降级逐张单图
        parsed_pages: List[Tuple[str, Dict[str, Any]]] = []   # (来源照片, 解析页)
        if config.use_agent_parse() and len(b64s) > 1:
            for start in range(0, len(b64s), _AGENT_BATCH):
                rels = group["files"][start:start + _AGENT_BATCH]
                batch = b64s[start:start + _AGENT_BATCH]
                # 空 b64（该照片未读取成功）不参与联合，避免空图干扰跨页关联，
                # 记入失败清单；逐张循环只处理已读取到的照片
                valid = [(r, b) for r, b in zip(rels, batch) if b]
                for rel, b64 in zip(rels, batch):
                    if not b64:
                        entry["failed_files"].append(rel)
                if len(valid) > 1:
                    try:
                        parsed_pages.append(("", _agent_pages(
                            [b for _, b in valid], exam)))
                        continue
                    except agent_chat.AgentChatError as exc:
                        logger.warning("多图联合解析失败，该批降级逐张解析：%s", exc)
                for rel, b64 in valid:
                    try:
                        parsed_pages.append((rel, _parse_page(b64, exam)))
                    except ocr.OCRError as exc:
                        logger.warning("批改读图失败（%s）：%s", rel, exc)
                        entry["failed_files"].append(rel)
        else:
            for rel, b64 in zip(group["files"], b64s):
                try:
                    parsed_pages.append((rel, _parse_page(b64, exam)))
                except ocr.OCRError as exc:
                    logger.warning("批改读图失败（%s）：%s", rel, exc)
                    entry["failed_files"].append(rel)
        for rel, parsed in parsed_pages:
            if parsed.get("markdown"):
                tag = "【%s】\n" % rel.split("/")[-1] if rel else "【多页合并】\n"
                extract_mds.append(tag + parsed["markdown"].strip())
            if parsed["name"]:
                ocr_names.append(parsed["name"])
            for q in parsed["questions"]:
                skey = _section_key(q.get("section"))
                # 序号与完整标题在 section_full（agent 路核心名无序号；
                # OCR 路无 section_full，退回 section）
                ordv = _section_ordinal(q.get("section_full") or "") \
                    or _section_ordinal(q.get("section"))
                if ordv is not None:
                    section_ord.setdefault(skey, ordv)
                # 展示标题取最完整的一份：section_full（agent 路含序号+说明的
                # 完整标题）优先，OCR 路无 full 退回 section（带序号者更长优先）
                cand = str(q.get("section_full") or "").strip() \
                    or str(q.get("section") or "").strip()
                if len(cand) > len(canonical.get(skey) or ""):
                    canonical[skey] = cand
                cur = merged.get((skey, q["no"]))
                if cur is None:
                    merged[(skey, q["no"])] = dict(q, file=rel)
                    continue
                # question 按质量择优：文章页的含空句子替换选项页的选项串，
                # 避免后页先处理时垃圾题干占位挤掉好题干
                if _question_quality(q) > _question_quality(cur):
                    cur["question"] = q["question"]
                for field in ("qtype", "options", "section_full",
                              "passage", "suggested_answer"):
                    if not cur.get(field) and q.get(field):
                        cur[field] = q[field]
                # section 偏好带序号的版本：大题标题只在其中一张照片上，
                # 带序号才能按书本大题顺序排序（如 "IV 完形填空" 优先于 "完形填空"）
                if _section_ordinal(q.get("section")) is not None \
                        and _section_ordinal(cur.get("section")) is None:
                    cur["section"] = q["section"]
                if not cur["student_answer"] and q["student_answer"]:
                    cur["student_answer"] = q["student_answer"]
                    cur["file"] = rel
        # 文件夹名未匹配名册时，用 OCR 读出的姓名再试（匹配结果仅供老师确认/改选）
        if not entry["matched"] and ocr_names:
            for nm in ocr_names:
                by_ocr = match_roster(nm, class_name)
                if by_ocr["matched"]:
                    entry.update(by_ocr)
                    entry["name_source"] = "ocr"
                    break
        # 书本每个大题从 1 重编号（I-1/II-1/III-1…），裸题号会互踩丢失；
        # 按大题序号（书本顺序）+ 大题内题号排序后全局重编号——学生常把后一页
        # 先拍（无标题照片的 section 不带序号，靠与带标题照片合并补回序号），
        # 页内解析又按裸题号排序（不同大题同号题会交错），插入序不可直接用；
        # no 全局唯一才能作前端题号与批改联接键
        section_rank: Dict[str, int] = {}

        def _order(item):
            (skey, no), _q = item
            rank = section_rank.setdefault(skey, len(section_rank))
            ordv = section_ord.get(skey)
            # 有序号的大题按书本序号排前，无序号的（推断失败/未合并到）按首次出现排后
            return (0, ordv, rank, no) if ordv is not None else (1, 0, rank, no)

        # 同一大题内统一 section 显示：后页照片常不印大题标题，其推断 section
        # 不带序号（"完形填空" vs "IV 完形填空"），不统一会被前端渲染成两个分组
        ordered = sorted(merged.items(), key=_order)
        entry["questions"] = [
            dict(q, no=i, section=canonical.get(_sk, q.get("section")))
            for i, ((_sk, _no), q) in enumerate(ordered, 1)]
        if extract_mds:
            entry["extract_mds"] = extract_mds
        if not entry["questions"]:
            entry["ocr_error"] = ("照片识别失败，可重拍或移除该生的照片"
                                  if len(entry["failed_files"]) == len(group["files"])
                                  else "未从照片中解析出题目，请核对照片内容")
        papers.append(entry)
    return {"meta": plan["meta"], "warnings": plan["warnings"], "papers": papers}
