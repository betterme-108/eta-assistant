"""英语教学助手 English Teaching Assistant 应用包。

分层结构（v4）：
  core/       配置中心（.env 双通道、LLM/OCR 通道参数）
  providers/  外部能力接入层（llm：OpenAI 兼容直连；ocr：mineru/paddleocr/vision）
  data/       数据层（db：SQLite 多班级；ontology：36 类本体库）
  services/   业务服务层（checking / papers / classifier / practice / decision / report）
  api/        HTTP 路由层（schemas + routes + check_routes）
  main.py     FastAPI 组装入口

兼容层：以下 re-export 让旧版 `from app import db, config, ...` 继续可用
（tests 与脚本依赖；新代码请使用子包路径导入）。
"""
from .core import config  # noqa: F401
from .data import db, ontology  # noqa: F401
from .providers import llm, ocr  # noqa: F401
from .services import classifier, decision, practice, report  # noqa: F401

__all__ = [
    "config", "db", "ontology", "llm", "ocr",
    "classifier", "decision", "practice", "report",
]
