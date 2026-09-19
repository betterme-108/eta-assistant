"""配置中心：.env 文件 + 环境变量双通道，支持测试库注入。

分层架构中的位置：core 层，不依赖任何其他 app 模块。

.env 变量规范（键名与 .env.example 保持一致，复制即可生效）：
  # ── 运行模式 ──
  ETA_DB=runtime/db/eta.db   # 兼容旧名 XIAOYING_DB / ERROR_RADAR_DB / ERROR_ATLAS_DB
  LOG_LEVEL=INFO                 # 终端日志级别（DEBUG/INFO/WARNING/ERROR）
  LOG_FILE_LEVEL=DEBUG           # 文件日志级别（默认 DEBUG，最全）
  LOG_DIR=runtime/logs           # 日志目录（默认项目根下 runtime/logs/）
  LOG_KEEP_DAYS=14               # 按天轮转保留天数，过期自动删除
  TRACE_ENABLED=1                # AI 运行留痕（输入输出细节落盘，0=关闭）
  TRACE_KEEP_DAYS=14             # 留痕文件保留天数，过期自动删除

  # ── LLM（OpenAI 兼容直连；deepseek* 前缀自动路由 DeepSeek 官方 API）──
  LLM_MODEL=deepseek-v4-pro
  LLM_TEMPERATURE=0.1
  LLM_MAX_TOKENS=8192
  LLM_TIMEOUT=60
  DEEPSEEK_BASE_URL=https://api.deepseek.com
  DEEPSEEK_API_KEY=sk-xxx
  LLM_BASE_URL=...            # 非 deepseek 模型的 OpenAI 兼容端点
  LLM_API_KEY=...

  # ── OCR（错题照片 → 文本）──
  OCR_PROVIDER=mineru        # mineru / paddleocr / vision / dashscope / doubao
  # mineru（文档解析服务，错题照片走 is_ocr）
  MINERU_BASE_URL=https://mineru.net
  MINERU_TOKEN=xxx
  MINERU_MODEL_VERSION=vlm   # pipeline / vlm
  # paddleocr（异步 jobs 流程：提交 → 轮询 → 下载）
  PADDLEOCR_BASE_URL=https://paddleocr.aistudio-app.com/api/v2/ocr
  PADDLEOCR_TOKEN=xxx
  PADDLEOCR_MODEL=PaddleOCR-VL-1.6
  # vision（通用 OpenAI 兼容视觉模型直连）
  VISION_BASE_URL=...         # 如 https://dashscope.aliyuncs.com/compatible-mode/v1
  VISION_API_KEY=xxx
  VISION_MODEL=qwen-vl-plus
  # 快捷通道（自动组装 vision 端点）
  DASHSCOPE_API_KEY=xxx       # OCR_PROVIDER=dashscope
  DOUBAO_API_KEY=xxx          # OCR_PROVIDER=doubao
  DOUBAO_MODEL=doubao-1.5-vision-pro-32k

  # ── 拍照批改解析（试卷照片 → 题目/作答/参考答案）──
  # 主路选择：agent（火山智能体会话，多模态直读整页结构化）优先，凭据未配置
  # 或调用失败时自动走 ocr 转录降级路；PHOTO_PARSE_PROVIDER=ocr 可强制纯 OCR。
  PHOTO_PARSE_PROVIDER=agent
  AGENT_CHAT_BOT_ID=<bot_id>             # 联网问答Agent 控制台的智能体 ID
  AGENT_CHAT_API_KEY_SECRET=xxx             # API Key 接入（推荐，Bearer 免签名）
  AGENT_CHAT_ACCESS_KEY_ID=xxx              # AK/SK 接入（备选，火山 v4 签名）
  AGENT_CHAT_SECRET_ACCESS_KEY=xxx          # 未填 API Key Secret 时启用 AK/SK
  # 转录降级路：不设则沿用 OCR_PROVIDER
  #   qwen_vl_ocr  阿里百炼 qwen-vl-ocr（复用 DASHSCOPE_API_KEY，试卷/手写优化）
  #   dual         mineru + paddleocr 双转录互补（快通道为主、慢通道补漏，
  #               融合由解析 LLM 完成；任一通道失败自动降级单转录）
  DOC_OCR_PROVIDER=qwen_vl_ocr
  QWEN_VL_OCR_MODEL=qwen-vl-ocr
"""
import os
from typing import Optional

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(APP_DIR)

# 应用版本号（全项目单一来源：API /health、FastAPI 文档、前端 UI-VERSION 均以此为准）
APP_VERSION = "v1.0.0"
ONTOLOGY_PATH = os.path.join(PROJECT_ROOT, "data", "error_ontology_en.yaml")
STATIC_DIR = os.path.join(APP_DIR, "static")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "runtime")   # 本机运行时产物（db/logs/seed）
DB_DIR = os.path.join(RUNTIME_DIR, "db")

DEFAULT_LLM_MODEL = "deepseek-v4-pro"


# ---------------------------------------------------------------- .env 加载

def load_dotenv(path: str = ENV_FILE, override: bool = False) -> None:
    """极简 .env 解析（KEY=VALUE，忽略注释与空行；不引入额外依赖）。

    默认不覆盖已存在的环境变量（环境变量优先于 .env 文件）。
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.partition("#")[0].strip().strip("'\"")
            if key and (override or key not in os.environ):
                os.environ[key] = value


load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------- 路径与模式

def get_db_path() -> str:
    """数据库路径。ETA_DB 环境变量支持测试库注入（兼容旧名 XIAOYING_DB / ERROR_RADAR_DB / ERROR_ATLAS_DB）。"""
    default = os.path.join(DB_DIR, "eta.db")
    return (_get("ETA_DB") or _get("XIAOYING_DB")
            or _get("ERROR_RADAR_DB") or _get("ERROR_ATLAS_DB", default))


LOG_LEVEL = _get("LOG_LEVEL", "INFO").upper()
# 说明：系统仅保留真实 API 通道（LLM + OCR），无 mock 降级；
# key 未配置时对应功能显式报错（fail-fast），避免静默给出不可信结果。


# ---------------------------------------------------------------- 日志系统
# 终端显示 LOG_LEVEL（默认 INFO）；文件按天轮转存 LOG_FILE_LEVEL（默认 DEBUG，
# 便于问题定位），保留 LOG_KEEP_DAYS 天后自动删除（日志管理见 app/core/logging_setup.py）。

LOG_DIR = _get("LOG_DIR") or os.path.join(RUNTIME_DIR, "logs")
LOG_KEEP_DAYS = _get_int("LOG_KEEP_DAYS", 14)
LOG_FILE_LEVEL = _get("LOG_FILE_LEVEL", "DEBUG").upper()

# AI 运行留痕（trace）：每次 LLM/OCR/批改API 调用的输入输出细节落盘为
# LOG_DIR/eta-trace-YYYY-MM-DD.jsonl（一行一调用），便于查运行效果；
# 保留 TRACE_KEEP_DAYS 天自动清理；TRACE_ENABLED=0 可关闭（详见 app/core/trace.py）。
TRACE_ENABLED = _get("TRACE_ENABLED", "1") not in ("0", "false", "no")
TRACE_KEEP_DAYS = _get_int("TRACE_KEEP_DAYS", 14)


# ---------------------------------------------------------------- LLM 通道
# direct 通道（OpenAI 兼容直连）：deepseek* 前缀走 DEEPSEEK_*，其余走 LLM_*

LLM_MODEL = _get("LLM_MODEL", DEFAULT_LLM_MODEL)
LLM_TEMPERATURE = _get_float("LLM_TEMPERATURE", 0.1)
LLM_MAX_TOKENS = _get_int("LLM_MAX_TOKENS", 8192)
LLM_TIMEOUT = _get_float("LLM_TIMEOUT", 60.0)              # 秒（读取等待；归因/练习均小任务，正常 3-20s）
LLM_CONNECT_TIMEOUT = _get_float("LLM_CONNECT_TIMEOUT", 30.0)
LLM_RETRIES = _get_int("LLM_RETRIES", 1)                    # 网络失败重试次数

DEEPSEEK_BASE_URL = _get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY")
LLM_BASE_URL = _get("LLM_BASE_URL").rstrip("/")
LLM_API_KEY = _get("LLM_API_KEY")


def llm_endpoint(model: str = "") -> tuple:
    """返回 (base_url, api_key)：deepseek* 模型自动路由 DeepSeek 官方 API。"""
    m = model or LLM_MODEL
    if m.lower().startswith("deepseek"):
        return DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY or LLM_API_KEY
    return LLM_BASE_URL or DEEPSEEK_BASE_URL, LLM_API_KEY or DEEPSEEK_API_KEY


def llm_available(model: str = "") -> bool:
    """当前配置是否具备真实调用条件（有端点且有 key）。"""
    base, key = llm_endpoint(model)
    return bool(base and key)


def is_deepseek_model(model: str = "") -> bool:
    """deepseek V4 系思考链与正文共享 max_tokens 预算，本项目需要确定性
    JSON 输出，故对 deepseek* 模型统一附加 thinking=disabled。"""
    return (model or LLM_MODEL).lower().startswith("deepseek")


def is_thinking_chain_model(model: str = "") -> bool:
    """需要禁用思考链的模型：deepseek* 与豆包 seed 系（思考与正文共享
    max_tokens 预算，长思考可致正文 0 token，本系统需要确定性 JSON）。"""
    name = (model or LLM_MODEL).lower()
    return name.startswith("deepseek") or name.startswith("doubao-seed")


# ---------------------------------------------------------------- 视觉 OCR

OCR_PROVIDER = _get("OCR_PROVIDER", "mineru").lower()        # mineru/paddleocr/vision/dashscope/doubao
OCR_TIMEOUT = _get_float("OCR_TIMEOUT", 120.0)
OCR_POLL_INTERVAL = _get_float("OCR_POLL_INTERVAL", 3.0)     # paddleocr 轮询间隔（秒）
OCR_POLL_MAX = _get_int("OCR_POLL_MAX", 100)                 # 轮询上限（次）
OCR_RETRIES = _get_int("OCR_RETRIES", 3)                     # mineru 链路抖动整流程重试（退避 2s/4s）

# mineru（文档解析服务，v4 批量上传流程）
MINERU_BASE_URL = _get("MINERU_BASE_URL", "https://mineru.net")
MINERU_TOKEN = _get("MINERU_TOKEN")
MINERU_MODEL_VERSION = _get("MINERU_MODEL_VERSION", "vlm")

# paddleocr（异步 jobs 流程）
PADDLEOCR_BASE_URL = _get("PADDLEOCR_BASE_URL",
                          "https://paddleocr.aistudio-app.com/api/v2/ocr")
PADDLEOCR_TOKEN = _get("PADDLEOCR_TOKEN")
PADDLEOCR_MODEL = _get("PADDLEOCR_MODEL", "PaddleOCR-VL-1.6")

# 通用 OpenAI 兼容视觉模型（显式配置）
VISION_BASE_URL = _get("VISION_BASE_URL").rstrip("/")
VISION_API_KEY = _get("VISION_API_KEY")
VISION_MODEL = _get("VISION_MODEL", "qwen-vl-plus")

# ── 作业解析双流（拍照批改用；详见 photo_check 模块注释）──
# 文档流通道（qwen_vl_ocr / dual / mineru / paddleocr；空 = 沿用 OCR_PROVIDER，
# 旧部署行为不变）。dual = mineru + paddleocr 双转录互补，融合由解析 LLM 完成。
DOC_OCR_PROVIDER = _get("DOC_OCR_PROVIDER", "").lower()
QWEN_VL_OCR_MODEL = _get("QWEN_VL_OCR_MODEL", "qwen-vl-ocr")

# ── 拍照批改解析主路（试卷照片 → 题目/作答/参考答案）──
# agent：火山智能体会话（联网问答Agent chat/completion），多模态照片直读
#        整页结构化解析；ocr：OCR 转录 + 解析 LLM（降级路，通道随 DOC_OCR_PROVIDER）。
# 选 agent 但凭据未配置时自动落到 ocr 路（旧部署行为不变）。
PHOTO_PARSE_PROVIDER = _get("PHOTO_PARSE_PROVIDER", "agent").lower()

# 火山智能体会话（agent 路凭据）：bot_id 为控制台创建的智能体 ID；
# 鉴权两种接入任选其一，API Key Secret 已配置时优先（Bearer 免签名），
# 否则用 AK/SK（火山 TOP 网关 v4 签名，region/service 见下方固定项）
AGENT_CHAT_BOT_ID = _get("AGENT_CHAT_BOT_ID")
AGENT_CHAT_API_KEY_SECRET = _get("AGENT_CHAT_API_KEY_SECRET")
AGENT_CHAT_ACCESS_KEY_ID = _get("AGENT_CHAT_ACCESS_KEY_ID")
AGENT_CHAT_SECRET_ACCESS_KEY = _get("AGENT_CHAT_SECRET_ACCESS_KEY")
AGENT_CHAT_TIMEOUT = _get_float("AGENT_CHAT_TIMEOUT", 180.0)   # 单次 HTTP 超时（整页 JSON 生成耗时较长）
# 固定网关端点（AK/SK 签名的规范化 query/region/service 必须与 URL 一致）
AGENT_CHAT_API_URL = _get(
    "AGENT_CHAT_API_URL",
    "https://open.feedcoopapi.com/agent_api/agent/chat/completion")
AGENT_CHAT_AKSK_URL = _get(
    "AGENT_CHAT_AKSK_URL",
    "https://mercury.volcengineapi.com?Action=ChatCompletion&Version=2026-01-01")
AGENT_CHAT_REGION = _get("AGENT_CHAT_REGION", "cn-north-1")
AGENT_CHAT_SERVICE = _get("AGENT_CHAT_SERVICE", "ask_echo")


def agent_chat_available() -> bool:
    """智能体会话是否具备调用条件（bot_id 已配置且两种鉴权其一完整）。"""
    if not AGENT_CHAT_BOT_ID:
        return False
    return bool(AGENT_CHAT_API_KEY_SECRET
                or (AGENT_CHAT_ACCESS_KEY_ID and AGENT_CHAT_SECRET_ACCESS_KEY))


def use_agent_parse() -> bool:
    """拍照批改是否走智能体会话主路（参数启用且凭据完整）。"""
    return PHOTO_PARSE_PROVIDER == "agent" and agent_chat_available()


# 快捷通道（端点可改：如 dashscope 国际版 dashscope-intl.aliyuncs.com）
DASHSCOPE_API_KEY = _get("DASHSCOPE_API_KEY")
DASHSCOPE_VL_BASE_URL = _get(
    "DASHSCOPE_VL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
DOUBAO_API_KEY = _get("DOUBAO_API_KEY")
DOUBAO_MODEL = _get("DOUBAO_MODEL", "doubao-1.5-vision-pro-32k")
DOUBAO_VL_BASE_URL = _get(
    "DOUBAO_VL_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")


def vision_endpoint(provider: str = "") -> tuple:
    """返回视觉模型 (base_url, api_key, model)，按 provider 组装端点。"""
    p = (provider or OCR_PROVIDER).lower()
    if p == "dashscope":
        return DASHSCOPE_VL_BASE_URL, DASHSCOPE_API_KEY, VISION_MODEL
    if p == "doubao":
        return DOUBAO_VL_BASE_URL, DOUBAO_API_KEY, DOUBAO_MODEL
    # vision：完全自定义 OpenAI 兼容端点
    return VISION_BASE_URL, VISION_API_KEY, VISION_MODEL


def vision_available(provider: str = "") -> bool:
    base, key, _model = vision_endpoint(provider)
    return bool(base and key)


def paddleocr_available() -> bool:
    return bool(PADDLEOCR_BASE_URL and PADDLEOCR_TOKEN)


def mineru_available() -> bool:
    return bool(MINERU_BASE_URL and MINERU_TOKEN)


def qwen_vl_ocr_available() -> bool:
    return bool(DASHSCOPE_API_KEY)


def doc_ocr_provider() -> str:
    """拍照批改文档流实际通道（health/日志展示用）：未配置时沿用 OCR_PROVIDER。"""
    return DOC_OCR_PROVIDER or OCR_PROVIDER


def ocr_available() -> bool:
    """当前 OCR_PROVIDER 是否具备真实调用条件（health 展示用）。"""
    p = OCR_PROVIDER
    if p == "mineru":
        return mineru_available()
    if p == "paddleocr":
        return paddleocr_available()
    if p == "qwen_vl_ocr":
        return qwen_vl_ocr_available()
    if p == "dual":  # 双转录互补：至少一个通道可用即可（另一个自动降级）
        return mineru_available() or paddleocr_available()
    if p in ("vision", "dashscope", "doubao"):
        return vision_available(p)
    return False
