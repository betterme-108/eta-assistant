"""日志系统初始化：终端 + 本地文件双通道，按天轮转、自动清理。

分层架构中的位置：core 层，仅依赖 config，不依赖其他 app 模块。
main.py 导入后调用一次 setup_logging() 即可（幂等，可重复调用）。

设计要点：
  - 终端：StreamHandler，级别 config.LOG_LEVEL（默认 INFO）——只显示
    启动/停止、AI/OCR 调用、错误等有意义的事件，不刷访问日志；
  - 文件：TimedRotatingFileHandler 按天轮转（runtime/logs/eta.log，
    历史文件 eta.log.YYYY-MM-DD），级别 config.LOG_FILE_LEVEL
    （默认 DEBUG，最全，含每条访问日志，便于问题定位），
    保留 config.LOG_KEEP_DAYS 天自动删除；
  - 重启策略：不新建文件——同一天重启追加到当天文件，main.lifespan
    写入“系统启动/停止”分隔线区分会话；跨天自动切新文件；
  - 命名规范：业务代码统一 logging.getLogger("eta.<模块>"）；
  - 收敛第三方噪音：httpx/httpcore/urllib3 的 debug 极多，压到 WARNING；
  - uvicorn.access 关传播，访问日志由 main.py 中间件统一记（DEBUG，仅文件）。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from . import config

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _level_of(name: str, default: str = "INFO") -> int:
    return getattr(logging, name.upper(), getattr(logging, default))


def setup_logging() -> str:
    """初始化根 logger（终端 + 文件），返回日志目录；幂等。"""
    global _configured
    if _configured:
        return config.LOG_DIR
    _configured = True

    os.makedirs(config.LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # 总开关放开，由各 handler 自行控级

    # 终端通道（日常运行视角）
    console = logging.StreamHandler()
    console.setLevel(_level_of(config.LOG_LEVEL))
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件通道（排障视角，按天轮转 + 自动清理）
    file_handler = TimedRotatingFileHandler(
        os.path.join(config.LOG_DIR, "eta.log"),
        when="midnight", backupCount=config.LOG_KEEP_DAYS, encoding="utf-8",
    )
    file_handler.setLevel(_level_of(config.LOG_FILE_LEVEL, "DEBUG"))
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 第三方噪音收敛；uvicorn 主体保留启动信息，access 由中间件统一记
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").propagate = False

    logging.getLogger("eta").info(
        "日志已就绪：目录 %s（按天轮转，保留 %d 天，终端级别 %s / 文件级别 %s）",
        config.LOG_DIR, config.LOG_KEEP_DAYS, config.LOG_LEVEL, config.LOG_FILE_LEVEL)
    return config.LOG_DIR
