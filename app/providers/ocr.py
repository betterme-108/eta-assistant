"""OCR 接入层：错题照片 → 文本。Provider 化架构，按 OCR_PROVIDER 路由。

通道一览：
  mineru     MinerU 文档解析（v4 批量上传流程，参考 ContractPilot
             src/backend/engine/algorithms/ocr/mineru.py：
             POST file-urls/batch 申请链接 → PUT 上传 → 轮询 → 下载 ZIP 提取 full.md）
  paddleocr  PaddleOCR 云服务（异步 jobs 流程，参考 ContractPilot
             src/backend/engine/algorithms/ocr/paddle.py：
             POST /jobs 提交 → GET /jobs/{id} 轮询 → GET jsonUrl 下载）
  qwen_vl_ocr  阿里百炼 qwen-vl-ocr（OpenAI 兼容，复用 DASHSCOPE_API_KEY；
             官方定位即文档/表格/试卷/手写混合场景的文字提取）
  vision     通用 OpenAI 兼容视觉模型直连（通义 VL / 豆包 / 自建端点）
  dashscope  vision 快捷通道（自动组装通义兼容模式端点）
  doubao     vision 快捷通道（自动组装火山方舟端点）

作业解析（拍照批改，PHOTO_PARSE_PROVIDER 两个维度：agent 智能体会话主路 /
  ocr 转录降级路）：
  智能体会话主路（agent_chat.parse_page）多模态直读原图，整页结构化提取——
                          题干/选项/原文/学生手写作答/参考答案一次产出（无 OCR
                          中间损耗）；未配置/失败时降级本模块转录路
  转录降级路 ocr_page_multi() 印刷题干转录，dual 时 mineru + paddleocr 双转录
                          互补（带通道标记交给解析 LLM 融合，单通道失败降级）；
                          qwen_vl_ocr 走 ocr_page_with_boxes 附带行坐标

统一出参：{question, qtype, passage, note}，note 固定标注 "AI 生成"（合规要求）；
ocr_page() 返回整页原始转录（拍照切题用，一页可含多题）。
key 未配置时 fail-fast 抛 OCRError（不再降级 mock，请检查 .env）。

脱敏说明：上传内容仅为题目文本图像，不含学生姓名（录入界面只用代号）。
"""
import base64
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from ..core import config, trace
from . import llm

logger = logging.getLogger("eta.ocr")

_OCR_PROMPT = ("识别图中的英语题目，原样输出题目文本与选项（如有）；"
               "第一行输出题型（单选/语法选择/完形填空/阅读理解/口语应用/任务型阅读/"
               "完成句子/概要补全/书面表达/词语运用/其他；听力题不处理，请跳过），"
               "第二行起输出题目原文。不要输出任何解释。"
               "若图中含阅读理解或完形填空的完整原文，则改为：先输出【原文】标记行与完整原文，"
               "再输出【题目】标记行与题型、题目（题型与题目在同一标记段内，题型独占首行）。")

# 整页转录（拍照切题用）：一页可能含多题，只要原样全文，不做单题假设
_RAW_PROMPT = ("原样转录图片中的全部文本，按阅读顺序逐行输出（保留题号与选项字母），"
               "不要总结、不要翻译、不要输出任何解释。图中可能含多道题目。")

# qwen-vl-ocr advanced_recognition 内置任务的官方指令：逐行输出文字与旋转矩形
# 坐标 [cx, cy, width, height, angle]（坐标在 content 数组的 ocr_result.words_info，
# 需 chat_vision_parts 取原始结构，普通文本提取会丢）
_ADVANCED_RECOGNITION_PROMPT = "定位所有的文字行，并且返回旋转矩形 ([cx, cy, width, height, angle]) 的坐标结果。"


class OCRError(Exception):
    """OCR 识别失败（网络/鉴权/任务失败/超时/未配置）。"""


# ---------------------------------------------------------------- 转录清洗
# 文档解析通道（mineru/paddleocr）的 full.md 会带版面噪声，逐类剥离：
#   插图占位：MinerU ![](images/..) / PaddleOCR <div><img ..></div>（手写批注
#             常被当成插图切出去，只剩占位符，对切题/作答识别全是噪声）
#   LaTeX 包裹：PaddleOCR 把划线内容输出成 $ \\underline{\\text{..}} $，
#             展平为纯文本（内容可能是学生手写填入的词，需要保留）
#   批改符号：√ × ✓ 等常被粘连进正文（如 "## √ 阅读理解"）或单独成行，
#             全局剥离（英语作业里 × 只会是批改打叉，无数学乘号场景）
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_IMG_RE = re.compile(r"<img[^>]*>")
_EMPTY_DIV_RE = re.compile(r"<div[^>]*>\s*</div>")
_LATEX_UD_TEXT_RE = re.compile(r"\$\s*\\underline\s*\{\\text\s*\{([^{}]*)\}\}\s*\$")
_LATEX_UD_RE = re.compile(r"\$\s*\\underline\s*\{([^{}]*)\}\s*\$")
_LATEX_TEXT_RE = re.compile(r"\\text\s*\{([^{}]*)\}")
_LATEX_UNDERLINE_RE = re.compile(r"\\underline\s*\{([^{}]*)\}")
_LATEX_DOLLAR_RE = re.compile(r"\$([^$]*\\[^$]*)\$")
_MARKS_RE = re.compile(r"[√✓✔✗✘☑☒❌×✕]+")


def _sanitize_transcript(text: str) -> str:
    """整页/单题转录清洗：去占位符与 LaTeX 包裹、滤批改符号，保留题干与作答。"""
    t = _clean_markdown(text)
    t = _MD_IMG_RE.sub("", t)
    t = _HTML_IMG_RE.sub("", t)
    t = _EMPTY_DIV_RE.sub("", t)
    t = _LATEX_UD_TEXT_RE.sub(r"\1", t)
    t = _LATEX_UD_RE.sub(r"\1", t)
    t = _LATEX_TEXT_RE.sub(r"\1", t)
    t = _LATEX_UNDERLINE_RE.sub(r"\1", t)
    t = _LATEX_DOLLAR_RE.sub(r"\1", t)
    t = _MARKS_RE.sub("", t)
    t = "\n".join(ln.rstrip() for ln in t.splitlines())
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ---------------------------------------------------------------- 图像规范化
# 识别前置预处理：EXIF 方向摆正 + 长边上限 + JPEG 重编码（前端 canvas 已压缩，
# 这里是服务端兑底——直接 curl/旧客户端的原图同样受益）。手机竖拍照片 EXIF
# 方向未摆正时识别端按旋转图解析，行序错乱是提取失准的常见根源；过大的原图
# （4000px+）在识别端被二次压缩后手写细节丢失。任何一步失败都原样返回
# （预处理只增强不阻断）。上限 2400 高于前端 2000：仅对更大的图生效，
# 前端已压缩过的图不会被二次重编码损耗。
_NORMALIZE_MAX_EDGE = 2400


def normalize_image(image_base64: str) -> str:
    """照片 base64 → 规范化 base64（EXIF 摆正 / 长边 ≤2400 / JPEG q88）。

    解码失败（非图片/已损坏/测试桩短字符串）时原样返回，绝不抛错。
    """
    b64 = (image_base64 or "").split(",", 1)[-1]
    if not b64 or len(b64) < 64:          # 过短不足成图（测试桩等），直接返回
        return image_base64
    try:
        from io import BytesIO

        from PIL import Image, ImageOps   # 按需导入：Pillow 仅预处理路径使用

        img = Image.open(BytesIO(base64.b64decode(b64)))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > _NORMALIZE_MAX_EDGE:
            scale = _NORMALIZE_MAX_EDGE / max(w, h)
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                             Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:                     # noqa: BLE001 —— 预处理失败不阻断识别
        return image_base64


# ---------------------------------------------------------------- 对外入口

def ocr_image(image_base64: str) -> Dict[str, str]:
    """图像 base64 → {question, qtype, passage, note}（单题假设）。按 OCR_PROVIDER 路由。"""
    raw = _sanitize_transcript(_ocr_raw(image_base64, _OCR_PROMPT))
    return _parse_reply(raw)


def ocr_page(image_base64: str) -> str:
    """整页原始转录（拍照切题/批改用）：一页多题/多题型时只取全文，切题交给后续 LLM。"""
    return _sanitize_transcript(_ocr_raw(image_base64, _RAW_PROMPT))


def ocr_page_multi(image_base64: str) -> List[Dict[str, str]]:
    """拍照批改文档流入口：按 DOC_OCR_PROVIDER 路由，返回转录列表。

    dual → [{channel: mineru, ...}, {channel: paddleocr, ...}]（互补融合由
    解析 LLM 完成）；其余通道（qwen_vl_ocr / mineru / paddleocr / vision 系）
    → 单元素列表。调用方（photo_check）不感知通道数，统一按列表融合。
    """
    mode = config.doc_ocr_provider()
    if mode == "dual":
        return ocr_page_dual(image_base64)["transcripts"]
    if mode == "qwen_vl_ocr":
        result = ocr_page_with_boxes(image_base64)
        return [{"channel": mode, "text": result["text"],
                 "lines": result["lines"]}]
    return [{"channel": mode, "text": ocr_page(image_base64)}]


def ocr_page_with_boxes(image_base64: str) -> Dict[str, Any]:
    """带行坐标的整页转录（题答关联用）：{text, lines}。

    qwen_vl_ocr 专属（advanced_recognition：每行文字的旋转矩形坐标
    [cx, cy, w, h, angle]，题号/作答就近归属的数据源）；
    其他通道无行坐标，lines 置空安全降级。
    """
    if not config.qwen_vl_ocr_available():
        return {"text": ocr_page(image_base64), "lines": []}
    try:
        content = llm.chat_vision_parts(
            image_base64, _ADVANCED_RECOGNITION_PROMPT,
            model=config.QWEN_VL_OCR_MODEL, temperature=0.0,
            base_url=config.DASHSCOPE_VL_BASE_URL,
            api_key=config.DASHSCOPE_API_KEY)
    except llm.LLMError as exc:
        raise OCRError("qwen_vl_ocr 坐标转录失败：%s" % exc)
    lines = _parse_ocr_lines(content)
    text = _sanitize_transcript(_parts_text(content))
    if not text.strip() and not lines:
        raise OCRError("qwen_vl_ocr 坐标转录返回为空")
    return {"text": text, "lines": lines}


def _parts_text(content: Any) -> str:
    """数组 content → 逐 part 拼接 text；字符串 content 原样返回。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "")
                          for p in content if isinstance(p, dict))
    return ""


def _parse_ocr_lines(content: Any) -> List[Dict[str, Any]]:
    """content → [{text, box}]（行文字与坐标 [cx, cy, w, h]，角度不使用）。

    兼容两种结构：content 数组元素的 ocr_result.words_info（官方文档形态），
    以及 words_info 直出（响应结构微调时兜底）。无效行（无文本/无坐标）跳过。
    """
    words: List[Dict[str, Any]] = []
    parts = content if isinstance(content, list) else [content]
    for part in parts:
        if not isinstance(part, dict):
            continue
        ocr_result = part.get("ocr_result")
        if ocr_result is None and isinstance(part.get("words_info"), list):
            ocr_result = part
        for w in (ocr_result or {}).get("words_info") or []:
            if not isinstance(w, dict):
                continue
            text = str(w.get("text") or "").strip()
            box = w.get("box")
            if not text or not isinstance(box, (list, tuple)) or len(box) < 3:
                continue
            try:
                words.append({"text": text, "box": [float(v) for v in box[:4]]})
            except (TypeError, ValueError):
                continue
    return words


def ocr_page_dual(image_base64: str) -> Dict[str, Any]:
    """dual 文档流：mineru + paddleocr 双转录互补（实测互补：MinerU 快但偶漏
    作答标记，PaddleOCR 作答识别全但慢 40-80s）。

    两份“脏法不同”的转录不做行级对齐（成本高且脆），带通道标记一起交给
    解析 LLM 互补合并。单通道失败自动降级单转录（MinerU 链路抖动实测四六
    开，dual 的另一半即天然重试）；两通道全失败才抛错。
    返回 {transcripts: [{channel, text}], failed: [channel]}，文本均已清洗。
    """
    transcripts: List[Dict[str, str]] = []
    failed: List[str] = []
    for name in ("mineru", "paddleocr"):
        try:
            text = _sanitize_transcript(_ocr_raw(image_base64, _RAW_PROMPT, provider=name))
        except OCRError as exc:
            logger.warning("dual 文档流 %s 通道失败（降级继续）：%s", name, exc)
            failed.append(name)
            continue
        if text.strip():
            transcripts.append({"channel": name, "text": text})
        else:
            failed.append(name)
    if not transcripts:
        raise OCRError("dual 文档流两通道均失败（mineru/paddleocr），请稍后重试或检查 .env")
    return {"transcripts": transcripts, "failed": failed}


def _ocr_raw(image_base64: str, vision_prompt: str, provider: str = "") -> str:
    """按 OCR_PROVIDER 路由，统一返回原始文本（解析由调用方负责）。

    provider 显式指定时覆盖全局（dual 双转录逐通道调用）。
    mineru/paddleocr 通道不走 LLM 客户端，此处单独落运行留痕；
    LLM 系通道（qwen_vl_ocr/vision/dashscope/doubao）由 llm._request 留痕覆盖。
    """
    provider = (provider or config.OCR_PROVIDER).lower()
    if provider == "mineru":
        if not config.mineru_available():
            raise OCRError("mineru 通道未配置 MINERU_TOKEN（请检查 .env）")
        fn = _mineru
    elif provider == "paddleocr":
        if not config.paddleocr_available():
            raise OCRError("paddleocr 通道未配置 PADDLEOCR_TOKEN（请检查 .env）")
        fn = _paddleocr
    elif provider == "qwen_vl_ocr":
        if not config.qwen_vl_ocr_available():
            raise OCRError("qwen_vl_ocr 通道未配置 DASHSCOPE_API_KEY（请检查 .env）")
        fn = lambda b64: _qwen_vl_ocr_raw(b64, vision_prompt)  # noqa: E731
    elif provider in ("vision", "dashscope", "doubao"):
        if not config.vision_available(provider):
            raise OCRError("%s 通道未配置 API key（请检查 .env）" % provider)
        fn = lambda b64: _vision_raw(b64, provider, vision_prompt)  # noqa: E731
    else:
        raise OCRError("未知 OCR_PROVIDER：%r（可选 mineru/paddleocr/qwen_vl_ocr/"
                       "vision/dashscope/doubao）" % provider)
    t0 = time.time()
    traced = provider in ("mineru", "paddleocr")
    in_brief = {"image_base64_len": len(image_base64 or ""),
                "prompt": (vision_prompt or "")[:120]}
    try:
        text = fn(image_base64)
    except OCRError as exc:
        if traced:
            trace.record("ocr", input=in_brief, error=exc,
                         meta={"provider": provider,
                               "latency_ms": int((time.time() - t0) * 1000)})
        raise
    except Exception as exc:  # noqa: BLE001 —— OCR 失败给出可读错误
        wrapped = OCRError("%s 通道识别失败：%s" % (provider, exc))
        if traced:
            trace.record("ocr", input=in_brief, error=wrapped,
                         meta={"provider": provider,
                               "latency_ms": int((time.time() - t0) * 1000)})
        raise wrapped
    if traced:
        trace.record("ocr", input=in_brief, output=text,
                     meta={"provider": provider,
                           "latency_ms": int((time.time() - t0) * 1000)})
    return text


def _clean_markdown(text: str) -> str:
    """清理 Markdown 转义（如填空下划线 \\_ → __），保留原文其余部分。"""
    return (text or "").replace("\\_", "_").strip()


def provider_name() -> str:
    """当前生效的 OCR 通道名（健康检查展示用）。"""
    return config.OCR_PROVIDER


# ---------------------------------------------------------------- vision（OpenAI 兼容视觉模型）

def _vision_raw(image_base64: str, provider: str, prompt: str) -> str:
    """通用视觉模型直连：复用统一 LLM 客户端（连接池/重试/容错），返回原始文本。

    端点用 vision_endpoint(provider) 组装的 base/key（dashscope/doubao 快捷
    通道在此生效），不走全局 LLM_*——修复此前组装结果被丢弃、实际路由到
    全局 LLM 端点的问题。
    """
    base, key, model = config.vision_endpoint(provider)
    return llm.chat_vision(image_base64, prompt, model=model, temperature=0.1,
                           base_url=base, api_key=key)


def _qwen_vl_ocr_raw(image_base64: str, vision_prompt: str) -> str:
    """qwen-vl-ocr 文档流（百炼 OpenAI 兼容，复用 DASHSCOPE_API_KEY）。

    模型本身面向文档/表格/试卷/手写混合场景，转录质量以印刷题干为主。
    """
    return llm.chat_vision(image_base64, vision_prompt,
                           model=config.QWEN_VL_OCR_MODEL, temperature=0.0,
                           base_url=config.DASHSCOPE_VL_BASE_URL,
                           api_key=config.DASHSCOPE_API_KEY)


def _parse_reply(content: str) -> Dict[str, str]:
    """模型回复 → {question, qtype, passage}。

    两段式约定（阅读/完形）：文本含【原文】/【题目】标记时拆出 passage；
    拆分后再按首行题型拆 qtype（与单段式一致）。
    """
    text = (content or "").strip()
    passage = ""
    m = re.search(r"【原文】\s*(.*?)\s*【题目】", text, re.S)
    if m:
        passage = m.group(1).strip()
        text = text[m.end():].strip()
    qtype = ""
    lines = text.splitlines()
    known = ("单选", "语法选择", "完形填空", "阅读理解", "口语应用", "任务型阅读",
             "完成句子", "概要补全", "书面表达", "词语运用", "其他")
    if lines:
        first = lines[0].strip()
        if first in known:
            qtype = first
            text = "\n".join(lines[1:]).strip()
    return {"question": text, "qtype": qtype, "passage": passage, "note": "AI 生成"}


# ---------------------------------------------------------------- mineru（v4 批量上传流程）

def _mineru(image_base64: str) -> Dict[str, str]:
    """MinerU 文档解析（含链路抖动重试）：申请链接 → PUT 上传 → 轮询 → ZIP 提取。

    流程（https://mineru.net/apiManage/docs，参考 ContractPilot）：
      1. POST /api/v4/file-urls/batch           → batch_id + 预签名上传链接
      2. PUT  {file_url}                          → 上传图片（无须 Content-Type）
      3. GET  /api/v4/extract-results/batch/{id}  → 轮询 state
      4. GET  full_zip_url                        → 下载 ZIP → 提取 full.md
    错题照片必开 is_ocr（拍照场景）；文件名带扩展名便于服务端格式识别。

    重试策略（实测本机→ mineru.net 链路 TCP 握手抖动，单次成功率仅四六开）：
      内层 httpx.HTTPTransport(retries=2)：连接级自动重试；
      外层 OCR_RETRIES 次整流程重试（退避 2s/4s），重新申请链接即新 batch，
      旧任务由 MinerU 自行过期，重试无副作用。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, config.OCR_RETRIES + 1):
        try:
            return _mineru_once(image_base64)
        except OCRError as exc:
            last_exc = exc
            if attempt < config.OCR_RETRIES:
                wait = 2 * attempt
                logger.warning("MinerU 第 %d 次尝试失败（%s），%ds 后重试", attempt, exc, wait)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _mineru_once(image_base64: str) -> Dict[str, str]:
    """MinerU 单次完整流程（v4 批量上传）。"""
    base = config.MINERU_BASE_URL.rstrip("/")
    auth = {"Authorization": "Bearer %s" % config.MINERU_TOKEN}
    image_bytes = base64.b64decode(image_base64)
    # connect 超时独立压至 10s（正常握手 <4s）：链路坏时不让单次尝试拖满 30s
    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(transport=transport,
                      timeout=httpx.Timeout(config.OCR_TIMEOUT, connect=10.0)) as client:
        # Step 1: 申请上传链接（上传完成后系统自动提交解析任务）
        resp = client.post(
            "%s/api/v4/file-urls/batch" % base,
            headers=dict(auth, **{"Content-Type": "application/json"}),
            json={"files": [{"name": "question.jpg", "is_ocr": True}],
                  "model_version": config.MINERU_MODEL_VERSION},
        )
        if resp.status_code != 200:
            raise OCRError("申请上传链接失败 (HTTP %d): %s" % (resp.status_code, resp.text[:200]))
        data = resp.json()
        if data.get("code") != 0:
            raise OCRError("MinerU 创建任务失败: code=%s, msg=%s"
                           % (data.get("code"), data.get("msg")))
        payload = data.get("data") or {}
        batch_id = payload.get("batch_id")
        upload_urls = payload.get("file_urls") or []
        if not batch_id or not upload_urls:
            raise OCRError("返回格式异常: %s" % str(data)[:200])
        logger.info("MinerU batch=%s 创建，上传图片 (%d bytes)", batch_id, len(image_bytes))

        # Step 2: PUT 上传到预签名 URL（无须 Authorization/Content-Type）
        put = client.put(upload_urls[0], content=image_bytes)
        if put.status_code not in (200, 201, 204):
            raise OCRError("上传失败 (HTTP %d): %s" % (put.status_code, put.text[:200]))

        # Step 3 & 4: 轮询 → 下载 ZIP → 提取 full.md（Markdown 转义由调用方清理）
        text = _poll_mineru_batch(client, auth, base, batch_id)
    return text


def _poll_mineru_batch(client: httpx.Client, auth: Dict[str, str],
                       base: str, batch_id: str) -> str:
    """轮询批量任务直到完成 → 返回 Markdown 文本。"""
    for attempt in range(1, config.OCR_POLL_MAX + 1):
        time.sleep(config.OCR_POLL_INTERVAL)
        resp = client.get("%s/api/v4/extract-results/batch/%s" % (base, batch_id),
                          headers=auth)
        if resp.status_code != 200:
            raise OCRError("查询任务失败 (HTTP %d): %s" % (resp.status_code, resp.text[:200]))
        data = resp.json()
        if data.get("code") != 0:
            raise OCRError("MinerU 查询失败: code=%s, msg=%s"
                           % (data.get("code"), data.get("msg")))
        for item in ((data.get("data") or {}).get("extract_result") or []):
            state = item.get("state", "")
            if state == "done":
                zip_url = item.get("full_zip_url", "")
                if not zip_url:
                    raise OCRError("任务完成但未返回 full_zip_url")
                logger.info("MinerU batch=%s 完成，下载结果", batch_id)
                return _mineru_zip_text(client, zip_url)
            if state == "failed":
                raise OCRError("解析失败: %s" % item.get("err_msg", "未知错误"))
            logger.debug("MinerU 轮询 #%d state=%s", attempt, state)
    raise OCRError("解析超时（超过 %d 次轮询）" % config.OCR_POLL_MAX)


def _mineru_zip_text(client: httpx.Client, zip_url: str) -> str:
    """下载结果 ZIP → 提取 full.md（优先），兜底任意 .md。"""
    import io
    import zipfile
    resp = client.get(zip_url)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        for suffix in ("full.md", ".md"):
            for name in names:
                if name.endswith(suffix):
                    return zf.read(name).decode("utf-8")
    raise OCRError("结果 ZIP 中未找到 Markdown 文件")


# ---------------------------------------------------------------- paddleocr（异步 jobs 流程）

def _paddleocr(image_base64: str) -> Dict[str, str]:
    """PaddleOCR 云服务：提交图片 → 轮询 → 下载结果（参考 ContractPilot）。

    流程（https://paddleocr.aistudio-app.com）：
      1. POST {base}/jobs        → multipart 上传图片 → jobId
      2. GET  {base}/jobs/{id}   → 轮询 state（pending/running/done/failed）
      3. GET  {resultUrl.jsonUrl} → 下载 JSONL → 提取每页 markdown.text
    """
    token = config.PADDLEOCR_TOKEN
    base = config.PADDLEOCR_BASE_URL.rstrip("/")
    headers = {"Authorization": "bearer %s" % token}
    image_bytes = base64.b64decode(image_base64)

    with httpx.Client(timeout=httpx.Timeout(config.OCR_TIMEOUT, connect=30.0)) as client:
        # Step 1: 提交图片 → jobId
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        resp = client.post(
            "%s/jobs" % base,
            headers=headers,
            data={"model": config.PADDLEOCR_MODEL,
                  "optionalPayload": _json_dumps(optional_payload)},
            files={"file": ("question.jpg", image_bytes, "image/jpeg")},
        )
        if resp.status_code != 200:
            raise OCRError("提交失败 (HTTP %d): %s" % (resp.status_code, resp.text[:200]))
        result = resp.json()
        job_id = (result.get("data") or {}).get("jobId")
        if not job_id:
            raise OCRError("提交返回格式异常: %s" % str(result)[:200])
        logger.info("PaddleOCR 提交图片 (%d bytes) → jobId=%s", len(image_bytes), job_id)

        # Step 2: 轮询直到 done/failed
        json_url = _poll_job(client, headers, base, job_id)

        # Step 3: 下载 JSONL → 提取 markdown 文本
        text = _download_text(client, json_url)
    return text


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _poll_job(client: httpx.Client, headers: Dict[str, str],
              base: str, job_id: str) -> str:
    """轮询任务状态直到完成 → 返回 jsonUrl。"""
    for attempt in range(1, config.OCR_POLL_MAX + 1):
        time.sleep(config.OCR_POLL_INTERVAL)
        resp = client.get("%s/jobs/%s" % (base, job_id), headers=headers)
        if resp.status_code != 200:
            raise OCRError("查询任务失败 (HTTP %d): %s" % (resp.status_code, resp.text[:200]))
        data = (resp.json() or {}).get("data") or {}
        state = data.get("state", "")
        if state == "done":
            logger.info("PaddleOCR jobId=%s 完成", job_id)
            try:
                return data["resultUrl"]["jsonUrl"]
            except (KeyError, TypeError):
                raise OCRError("完成响应缺少 resultUrl.jsonUrl")
        if state == "failed":
            raise OCRError("解析失败: %s" % data.get("errorMsg", "未知错误"))
        logger.debug("PaddleOCR 轮询 #%d state=%s", attempt, state)
    raise OCRError("解析超时（超过 %d 次轮询）" % config.OCR_POLL_MAX)


def _download_text(client: httpx.Client, jsonl_url: str) -> str:
    """下载 JSONL 结果 → 提取每页 markdown 文本拼接。"""
    import json
    resp = client.get(jsonl_url)
    resp.raise_for_status()
    parts: List[str] = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            result = json.loads(line)["result"]
            for res in result.get("layoutParsingResults", []):
                md = (res.get("markdown") or {}).get("text", "").strip()
                if md:
                    parts.append(md)
        except (KeyError, json.JSONDecodeError) as exc:
            logger.warning("PaddleOCR 跳过损坏的 JSONL 行: %s", exc)
    return "\n".join(parts)
