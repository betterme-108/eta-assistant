"""日志系统：前端错误上报 → 写入服务端日志（与后端日志汇流）。"""
import logging

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_client_log_report(caplog):
    """浏览器错误上报 → 写入服务端日志（与后端日志汇流）。"""
    with caplog.at_level(logging.ERROR, logger="eta.logs"):
        r = client.post("/api/logs/client", json={
            "level": "ERROR", "message": "页面脚本错误", "page": "#/scan",
            "stack": "at boot"})
    assert r.status_code == 200 and r.json()["ok"]
    assert any("页面脚本错误" in rec.message for rec in caplog.records)
