"""英语教学助手 English Teaching Assistant —— FastAPI 入口（v1.0.0：批改 + 归因 + 讲评一体化）。

分层架构：
  core/       配置中心（.env 双通道）+ 日志系统（终端 + 文件双通道）
  providers/  外部能力接入（LLM OpenAI 兼容直连 / OCR 多通道）
  data/       SQLite 数据层 + 错因本体库
  services/   业务服务（批改 / 归因 / 练习 / 决策 / 报告）
  api/        HTTP 路由与请求模型（业务 + 批改 + 日志管理）
  main.py     仅做应用组装（日志、访问日志中间件、异常兜底、路由挂载、静态页）

题型边界：本系统无语音链路（不做 ASR/TTS），听力等音频类题目不在范围内。
"""
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import check_routes, export_routes, log_routes, routes as api_routes
from .core.logging_setup import setup_logging
from .data import db

logger = logging.getLogger("eta.main")
access_logger = logging.getLogger("eta.access")

setup_logging()  # 终端 + 文件双通道（幂等；日志目录见 config.LOG_DIR）


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    logger.info("════════ 系统启动 ════════")
    yield
    logger.info("════════ 系统停止 ════════")


def create_app() -> FastAPI:
    app = FastAPI(
        title="英语教学助手 English Teaching Assistant", version=config.APP_VERSION,
        description="初中英语课时作业 / 考试试卷拍照批改 + 错因分析与讲评备课一体化系统（学生零参与）",
        lifespan=lifespan,
    )

    # 访问日志：方法/路径/状态/耗时——仅入文件（DEBUG），终端不刷屏
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 —— 记录后交给全局异常处理器
            access_logger.exception("请求异常 %s %s", request.method, request.url.path)
            raise
        access_logger.debug("%s %s → %d (%.0fms)", request.method,
                            request.url.path, response.status_code,
                            (time.perf_counter() - t0) * 1000)
        return response

    # 全局异常兜底：完整堆栈进日志，前端只收直白提示
    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):  # noqa: ARG001
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "系统遇到问题，请稍后重试；如果反复出现，请截图联系技术人员。"},
        )

    # API 路由（业务 + 批改 + 导出 + 日志管理）
    app.include_router(api_routes.router)
    app.include_router(check_routes.router)
    app.include_router(export_routes.router)
    app.include_router(log_routes.router)

    # 单页前端（零依赖静态 SPA）
    index_path = os.path.join(config.STATIC_DIR, "index.html")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        # 手动读文件返回：不走 FileResponse（会附带 ETag/Last-Modified，
        # 部分浏览器在 no-store 与 ETag 并存时仍可能命中 304 旧副本），
        # 用全套防缓存头彻底禁止浏览器/预览环境缓存 index.html。
        with open(index_path, encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(
            content,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # 拆分后的静态资源（css / js）：与 index.html 同款防缓存处理——
    # 去掉 ETag/Last-Modified（避免 304 旧副本），并加全套 no-store 头
    class NoCacheStaticFiles(StaticFiles):
        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            # MutableHeaders 无 pop 方法：先判存在再用 del 删除
            if "etag" in response.headers:
                del response.headers["etag"]
            if "last-modified" in response.headers:
                del response.headers["last-modified"]
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

    app.mount("/static", NoCacheStaticFiles(directory=config.STATIC_DIR), name="static")

    return app


app = create_app()
