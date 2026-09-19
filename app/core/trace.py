"""AI 运行留痕（trace）：把每次 AI/OCR/批改调用的输入输出细节落盘，便于查运行效果。

分层架构中的位置：core 层，仅依赖 config（与 logging_setup 平级）。
与日志系统（logging_setup，事件级文本）互补：trace 记录的是每次调用的
完整输入与输出数据（JSON），复盘解析效果、比对提示词改版前后差异用。

设计要点：
  - 文件：LOG_DIR/eta-trace-YYYY-MM-DD.jsonl（按天一文件，一行一调用）；
    文件名前缀与日志一致（eta），自动进入 /api/logs 的列表/查看/下载
    白名单，无需新增管理端点；
  - 保留 TRACE_KEEP_DAYS 天，写入口每天首次写入时自动清理过期文件；
  - 脱敏：超长 base64（图片/文件体）替换为 <base64:N字符>，密钥类字段打码
    ——留痕只在本机，但仍不落任何密钥明文；
  - 绝不影响业务：任何写盘异常只记 warning，不抛出、不改变调用结果；
  - TRACE_ENABLED=0 可整体关闭（.env）。

埋点方（providers/services 层调用 record(kind, input=…, output=…, error=…, meta=…)）：
  llm._request          kind="llm"          每次 LLM/视觉对话（含重试后终态）
  agent_chat.chat       kind="agent_chat"   智能体会话（拍照批改 agent 主路）的调用与回复
  ocr._ocr_raw          kind="ocr"          mineru/paddleocr 通道转录（LLM 系通道由 llm 覆盖）
  photo_check._parse_page kind="photo_parse" 一张照片的解析路由与产出题目
"""
import datetime
import glob
import json
import logging
import os
import re

from . import config

logger = logging.getLogger("eta.trace")

# 超长 base64（≥400 字符的连续 base64 字母表）视为图片/文件体，只记长度
_B64_RE = re.compile(r"[A-Za-z0-9+/=]{400,}")
# 密钥类字段名（命中即整体打码，防御性兜底——正常埋点不含密钥）
_SENSITIVE_KEYS = ("api_key", "token", "authorization", "password", "secret")

_last_cleanup = ""   # "YYYY-MM-DD"：每天首次写入时清理一次过期文件


def _mask(value):
    """递归脱敏：密钥字段打码、超长 base64 只记长度，其余结构原样保留。"""
    if isinstance(value, dict):
        return {k: ("<masked>" if any(s in str(k).lower() for s in _SENSITIVE_KEYS)
                    else _mask(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    if isinstance(value, str):
        return _B64_RE.sub(lambda m: "<base64:%d字符>" % len(m.group()), value)
    return value


def _cleanup_once() -> None:
    """每天首次写入时清理过期的 trace 文件（与日志按天轮转同节奏）。"""
    global _last_cleanup
    today = datetime.date.today().isoformat()
    if _last_cleanup == today:
        return
    _last_cleanup = today
    deadline = datetime.date.today() - datetime.timedelta(days=config.TRACE_KEEP_DAYS)
    for path in glob.glob(os.path.join(config.LOG_DIR, "eta-trace-*.jsonl")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.jsonl$", path)
        if not m:
            continue
        try:
            if datetime.date.fromisoformat(m.group(1)) < deadline:
                os.remove(path)
        except (ValueError, OSError) as exc:
            logger.warning("清理过期留痕失败（%s）：%s", path, exc)


def record(kind: str, *, input=None, output=None, error=None, meta=None) -> None:
    """追加一条运行留痕：一行 JSON（时间/类型/输入/输出/错误/元信息）。

    任何异常只记 warning——留痕失败绝不影响业务调用本身。
    """
    if not config.TRACE_ENABLED:
        return
    try:
        now = datetime.datetime.now()
        entry = {
            "ts": now.isoformat(timespec="milliseconds"),
            "kind": kind,
            "input": _mask(input),
            "output": _mask(output),
            "error": (str(error)[:2000] if error else ""),
            "meta": _mask(meta or {}),
        }
        os.makedirs(config.LOG_DIR, exist_ok=True)
        _cleanup_once()
        path = os.path.join(config.LOG_DIR, "eta-trace-%s.jsonl" % now.date())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 —— 留痕绝不阻断业务
        logger.warning("运行留痕写入失败：%s", exc)
