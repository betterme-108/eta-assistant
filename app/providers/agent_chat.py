"""火山智能体会话 API（联网问答Agent chat/completion）——试卷照片提取主路。

作业/试卷照片以多模态消息（图片 DataUrl + 简短提取指令）发给智能体
（bot_id），按 web 端同款方式自由输出 Markdown 提取文本：大题完整标题 /
原文素材 / 题干 / 学生作答标注（拍照批改 agent 路的第一步；结构化由
DeepSeek 在 photo_check 侧完成——图像理解归智能体，文本结构化归 LLM）。

鉴权两种接入方式（任选其一，API Key Secret 已配置时优先，无需签名）：
  API Key  POST {AGENT_CHAT_API_URL}
           Headers: Authorization: Bearer <API Key Secret> + ServiceName: ask_echo
  AK/SK    POST {AGENT_CHAT_AKSK_URL}（火山 TOP 网关，Action=ChatCompletion）
           火山引擎 v4 签名（ServiceName=ask_echo；签名方法遵循火山引擎官方
           规范：规范化请求 → 待签名串 → HMAC-SHA256 密钥派生链）

响应处理（非流式）：choices[0].message.content 为回复文本；业务错误以
{error: {code, message}} 出现在响应体，网关错误以 ResponseMetadata.Error
出现，均归一为 AgentChatError。

反标签化：进入第三方服务的只有试卷照片本身；学生姓名等任何标识
不进入本模块的任何请求（提取文本里的姓名仅用于本机名册匹配）。
"""
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..core import config, trace

logger = logging.getLogger("eta.providers.agent_chat")


class AgentChatError(Exception):
    """智能体会话 API 调用失败（网络/鉴权/业务错误/响应异常）。"""


_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    """共享连接池（与 llm 模块同款分层超时；整页 JSON 生成耗时较长）。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=httpx.Timeout(config.AGENT_CHAT_TIMEOUT, connect=15.0, write=60.0))
    return _client


def reset_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        _client.close()
    _client = None


# ---------------------------------------------------------------- 火山 v4 签名（AK/SK 接入）

def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _aksk_headers(host: str, query: str, body: bytes) -> Dict[str, str]:
    """AK/SK 接入请求头：火山 TOP 网关 v4 签名（ServiceName=ask_echo）。

    签名步骤（火山引擎签名方法规范）：
      规范化请求 = Method + Path + 排序后的 Query + 规范化 Headers +
                   签名 Headers + 请求体 SHA256
      待签名串   = "HMAC-SHA256" + X-Date + 凭证范围 + SHA256(规范化请求)
      签名密钥   = SK → 日期 → region → service → "request" 的 HMAC 派生链
    """
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    headers = {
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": x_date,
    }
    signed_headers = "content-type;host;x-date"   # 签名串中 header 名用小写（火山规范）
    canonical_headers = "".join(
        "%s:%s\n" % (name, value)
        for name, value in (
            ("content-type", "application/json"),
            ("host", host),
            ("x-date", x_date),
        ))
    canonical_request = "\n".join([
        "POST", "/", query, canonical_headers, signed_headers,
        hashlib.sha256(body).hexdigest(),
    ])
    scope = "%s/%s/%s/request" % (date_stamp, config.AGENT_CHAT_REGION,
                                  config.AGENT_CHAT_SERVICE)
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    key = config.AGENT_CHAT_SECRET_ACCESS_KEY.encode("utf-8")
    for part in (date_stamp, config.AGENT_CHAT_REGION,
                 config.AGENT_CHAT_SERVICE, "request"):
        key = _hmac_sha256(key, part)
    signature = _hmac_sha256(key, string_to_sign).hex()
    headers["Authorization"] = (
        "HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (config.AGENT_CHAT_ACCESS_KEY_ID, scope, signed_headers, signature))
    return headers


# ---------------------------------------------------------------- 会话补全

def chat(messages: List[Dict[str, Any]]) -> str:
    """智能体会话补全（非流式）→ 回复文本。

    请求体 {bot_id, stream: false, messages}；鉴权按配置自动路由
    （API Key Bearer / AK-SK 签名）。业务与网关错误均归一为
    AgentChatError；输入规模与回复全文落运行留痕（trace）。
    """
    if not config.agent_chat_available():
        raise AgentChatError("智能体会话未配置（.env 设 AGENT_CHAT_BOT_ID 与 "
                             "AGENT_CHAT_API_KEY_SECRET，或 AK/SK 两项）")
    payload = {"bot_id": config.AGENT_CHAT_BOT_ID,
               "stream": False, "messages": messages}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if config.AGENT_CHAT_API_KEY_SECRET:
        url, auth = config.AGENT_CHAT_API_URL, "api_key"
        headers = {
            "Authorization": "Bearer %s" % config.AGENT_CHAT_API_KEY_SECRET,
            "Content-Type": "application/json",
            "ServiceName": config.AGENT_CHAT_SERVICE,
        }
    else:
        url, auth = config.AGENT_CHAT_AKSK_URL, "aksk"
        parsed = urllib.parse.urlparse(url)
        headers = _aksk_headers(parsed.netloc, parsed.query, body)
    t0 = time.time()
    data = None
    for attempt in (1, 2):   # 瞬时网络失败重试 1 次（整页生成耗时长，不做多次）
        try:
            resp = _get_client().post(url, content=body, headers=headers)
            data = resp.json()    # 业务错误可能随非 200 状态码返回，先解析保留细节
            break
        except Exception as exc:  # noqa: BLE001 —— 网络层失败（连接/读取/解析）
            if attempt == 1:
                logger.warning("智能体会话请求失败，2s 后重试一次：%s", str(exc)[:120])
                time.sleep(2)
                continue
            raise AgentChatError("智能体会话接口请求失败：%s" % str(exc)[:200])
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict) and err:
        raise AgentChatError("智能体会话接口错误 %s：%s"
                             % (err.get("code"), str(err.get("message"))[:200]))
    err = ((data.get("ResponseMetadata") or {}).get("Error")) or {} \
        if isinstance(data, dict) else {}
    if err:
        raise AgentChatError("智能体会话网关错误 %s：%s"
                             % (err.get("Code"), str(err.get("Message"))[:200]))
    if resp.status_code >= 400:
        raise AgentChatError("智能体会话接口 HTTP %d：%s"
                             % (resp.status_code, str(data)[:200]))
    try:
        content = ((data["choices"][0].get("message") or {}).get("content")) or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AgentChatError("智能体会话接口返回格式异常（缺少 choices.message）")
    if not (isinstance(content, str) and content.strip()):
        raise AgentChatError("智能体会话接口返回 content 为空")
    logger.info("智能体会话调用完成 | 返回 %d 字，耗时 %.0fms",
                len(content), (time.time() - t0) * 1000)
    trace.record("agent_chat", input={"messages_len": len(messages)},
                 output=content,
                 meta={"bot_id": config.AGENT_CHAT_BOT_ID, "auth": auth,
                       "latency_ms": int((time.time() - t0) * 1000)})
    return content


# ---------------------------------------------------------------- 整页试卷提取

# 提取指令（照片 → Markdown 文本）：只让智能体做它最擅长的事——看图提取文本。
# 不强制 JSON（约束叠加智能体预设提示词后输出不稳），改用紧凑的 Markdown 协议
# （【大题】【素材】标识 + 题干行下紧跟「**学生作答：**」行）：标识加排版约束，
# 比文字性要求更能防止智能体自由发挥（概括/改写题干、自创大题标题）；结构化由
# DeepSeek 完成（photo_check._md_parse_prompt）——分工：图像理解归智能体，文本结构化归 LLM。
# 两种批改类型各自一套提取指令（对应各自的解析模板）：课时作业按「基础巩固/
# 能力提升」栏目提取，考试试卷按「听力部分/笔试部分」固定层级提取——照片属于
# 哪种类型就按哪套模板解析，防止把试卷解析成作业形态（或反过来）。
_EXTRACT_RULES = (
    "## 【大题】<大题的完整标题：照照片上印的原样，序号+名称+题目说明，\n"
    "如「Ⅳ 完形填空。从各题所给的四个选项中选择最佳答案（建议用时:6分钟，"
    "难度:★★）」。照片上没印大题标题时，按题目形态起标准名（如「完形填空」），\n"
    "不要自创「一、完形填空类大题」之类的名字>\n"
    "\n"
    "【素材】<该大题的印刷素材，照原样完整列出：完形填空/阅读理解的文章原文\n"
    "（文章里的空格编号就是小题号，照原样保留）、方框选词/选短语的候选清单；\n"
    "该大题没有文章或清单时整行省略>\n"
    "\n"
    "1. <印刷题干原文：逐字转录，空线/空位/括号提示/句中夹着的空格编号\n"
    "一律照原样保留；严禁概括、改写、翻译或补全（概括句不是题干）；选择题的\n"
    "选项紧接题干下一行：A. … B. … C. … D. …>\n"
    "**学生作答：<该生此题的手写作答；未作答写「未作答」>**\n"
    "\n"
    "2. …\n"
    "**学生作答：…**\n"
    "\n"
    "要求：\n"
    "- 排版紧凑：每道题就是一块——题干行（选择题选项紧随）下一行紧跟\n"
    "  「**学生作答：…**」，题与题之间不要空行，也不给每题写说明文字\n"
    "- 照片上每个大题都从「## 【大题】」起头（同页多个大题就多个起头），\n"
    "  大题内题号按大题内编号\n"
    "- 语篇题（完形填空/阅读理解）：文章原文放【素材】；完形每小题写选项行\n"
    "  （题号 + A. … B. … C. … D. …），作答紧跟；阅读每小题题干行 + 选项行，作答紧跟\n"
    "- 大题内有集中作答区（如「**学生填写答案：**」+ 编号列表）时照原样保留\n"
    "- 印刷题干与学生手写严格区分、互不混入：题干只含印刷文字，\n"
    "  学生手写只出现在「**学生作答：**」行里；无法辨认的手写照看到的原样输出\n"
    "- 学生作答的常见形态都要读进「**学生作答：**」行：题前括号里的字母、\n"
    "  写在题号旁或题目行尾的字母、直接写在空线上的词句、以及打钩（√）或画圈\n"
    "  选中的选项——钩/圈住哪个选项，该选项字母就是作答（作答只写该字母本身）\n"
    "- 填空题先分清「印刷已给」与「学生补写」：印刷体印出的字母前缀（如\n"
    "  incom___、v__getables 里已印的部分）、括号里的提示词（如 (great)、(be)）、\n"
    "  空线前后的印刷文字都照原样留在题干里，不进作答；学生手写补上去的\n"
    "  字母/单词/短语才是作答——作答只写学生补写的部分本身（哪怕只有一两个\n"
    "  字母）；学生把整个词重新写了一遍时，作答才写整个词\n"
    "- 自动过滤与作答无关的标记：涂改、订正、勾画圈画、手写批注、分数、\n"
    "  草稿笔迹与老师批改符号（老师红笔的钩叉不是学生作答，按批改符号过滤）；\n"
    "  页眉页脚、页码、栏目名、得分栏忽略\n"
    "- 只做结构化提取，不判对错、不添加解析、不修改原文\n"
    "- 除提取内容外不要输出任何说明文字")

# 课时作业版提取指令：作业按「基础巩固/能力提升」栏目组织，听力题跳过
_EXTRACT_PROMPT_LESSON = (
    "请对这张学生英语课时作业照片做「作业文本结构化提取」，严格按下面的标识模板"
    "用 Markdown 输出：\n"
    "照片是课时作业页：印刷栏目「基础巩固」「能力提升」下的各个题型都按大题提取，"
    "填空、单选、完形、阅读全部逐题逐条整理；听力题跳过。\n"
    "\n" + _EXTRACT_RULES)

# 考试试卷版提取指令：试卷按「听力部分/笔试部分」固定层级；听力题跳过（无语音链路）
_EXTRACT_PROMPT_EXAM = (
    "请对这张学生英语考试试卷照片做「试卷文本结构化提取」，严格按下面的标识模板"
    "用 Markdown 输出：\n"
    "照片是考试试卷，统一试卷层级：第一部分 听力部分、第二部分 笔试部分"
    "（单项选择 / 完形填空 / 阅读理解 / 词汇运用·句型转换 / 书面表达）。\n"
    "- 先在开头输出一行「# <试卷标题>（<年级>，<单元/月考/期末>）」，照片没印就省略\n"
    "- 听力部分整段跳过不提取：本系统不处理音频类题目，只提取第二部分笔试部分\n"
    "- 每个【大题】的完整标题带上所属部分（如「第二部分 笔试部分 · Ⅱ 单项选择」）；\n"
    "  阅读理解下设阅读A、阅读B时分开标大题\n"
    "- 书面表达大题：「**学生作答：**」行完整转录学生手写作文全文\n"
    "\n" + _EXTRACT_RULES)


def _extract_prompt(exam_type: str) -> str:
    """批改类型 → 提取指令：考试试卷用试卷模板，课时作业（或未知类型）用作业模板。"""
    return (_EXTRACT_PROMPT_EXAM if (exam_type or "").strip() == "考试试卷"
            else _EXTRACT_PROMPT_LESSON)


def extract_markdown(image_base64: str, exam_type: str = "") -> str:
    """一张作业/试卷照片 → Markdown 提取文本（agent 路第一步）。

    多模态消息 = 图片 DataUrl + 标识模板指令（exam_type 选模板：课时作业 /
    考试试卷两套）；返回智能体的 Markdown 回复（【大题】/【素材】标识 +
    题干行下紧跟「**学生作答：**」行的紧凑排版）。回复为空抛
    AgentChatError（调用方降级 OCR 转录路）。
    """
    b64 = (image_base64 or "").split(",", 1)[-1]     # 容错：剥掉可能存在的 DataUrl 头
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,%s" % b64}},
            {"type": "text", "text": _extract_prompt(exam_type)},
        ],
    }]
    return chat(messages)


# 多图联合提取的引导指令：学生照片常跨页（某大题的题干在一页、选项或作答在
# 另一页，同一题也可能跨页续排），单页各自提取会漏内容——多图一次交给智能体，
# 要求按大题与题号把多页内容关联起来合并输出。
_MULTI_PREFIX = (
    "以下是同一名学生本次作业/试卷的 %d 张照片（按页序排列）。大题可能跨页："
    "某大题的题干在一页、选项或学生作答在另一页，同一题也可能跨页续排。"
    "请把多张照片的内容关联起来合并输出：同一大题只输出一个「## 【大题】」标题，"
    "同一题只输出一块（题干/选项/作答分别取各页可见部分按题号归并），"
    "不要因分页重复输出同一题，也不要漏掉任一页的内容。\n\n")


def extract_markdown_multi(images: List[str], exam_type: str = "") -> str:
    """多张照片一次提取（agent 路多图联合）：跨页题干/选项/作答关联合并。

    content = 多张图片 DataUrl + 跨页合并引导 + 标识模板指令；返回合并后的
    Markdown 回复（同一大题一个标题、同一题一块）。回复为空抛
    AgentChatError（调用方降级逐张单图解析）。
    """
    parts = [{"type": "image_url",
              "image_url": {"url": "data:image/jpeg;base64,%s"
                            % (img or "").split(",", 1)[-1]}} for img in images]
    parts.append({"type": "text",
                  "text": _MULTI_PREFIX % len(images) + _extract_prompt(exam_type)})
    messages = [{"role": "user", "content": parts}]
    return chat(messages)
