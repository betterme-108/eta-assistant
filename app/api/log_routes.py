"""前端错误上报 API：浏览器侧错误写入服务端日志文件，与后端日志汇流。

页面脚本错误 / 请求失败 / 未处理拒绝统一上报到这里，便于整体定位；
日志文件本体在本机 runtime/logs 目录，直接拷贝给技术人员排查即可。
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/logs", tags=["logs"])

logger = logging.getLogger("eta.logs")


class ClientLogEntry(BaseModel):
    """前端错误上报结构（页面脚本错误 / 请求失败 / 未处理拒绝）。"""
    level: str = "ERROR"
    message: str
    page: str = ""
    stack: str = ""


@router.post("/client")
def client_log(entry: ClientLogEntry):
    """接收浏览器侧错误 → 写入服务端日志文件（与后端日志汇流，便于整体定位）。"""
    level = entry.level.lower()
    if level not in ("debug", "info", "warning", "error", "critical"):
        level = "error"
    text = "浏览器 [%s] 页面=%s %s" % (level.upper(), entry.page or "-", entry.message)
    if entry.stack:
        text += "\n" + entry.stack
    getattr(logger, level)(text)
    return {"ok": True}
