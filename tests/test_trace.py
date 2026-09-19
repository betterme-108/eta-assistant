"""AI 运行留痕（trace）：落盘、脱敏、按天清理、静默失败、开关。

留痕与日志互补：一行 JSON 记一次 AI/OCR 调用的完整输入输出，
复盘解析效果用；任何写盘异常绝不影响业务调用本身。
"""
import json
import os

from app.core import config, trace


def _enable(tmp_path, monkeypatch, keep_days=14):
    """在 tmp_path 下开启留痕（conftest 默认关闭，这里显式覆盖）。"""
    monkeypatch.setattr(config, "TRACE_ENABLED", True)
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(config, "TRACE_KEEP_DAYS", keep_days)
    trace._last_cleanup = ""   # 重置每日清理缓存（跨天跑测试不受影响）
    return config.LOG_DIR


def _read_all(log_dir):
    name = [n for n in os.listdir(log_dir) if n.startswith("eta-trace-")][0]
    with open(os.path.join(log_dir, name), encoding="utf-8") as f:
        return [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]


def test_record_persists_and_masks(tmp_path, monkeypatch):
    """正常落盘：一行 JSON 含类型/输入/输出/元信息；base64 只记长度、密钥打码。"""
    log_dir = _enable(tmp_path, monkeypatch)
    b64 = "A" * 500 + "/=+"
    trace.record("llm",
                 input={"messages": [{"role": "user", "content": b64}],
                        "api_key": "sk-secret-value"},
                 output={"content": "ok"},
                 meta={"model": "test-model", "latency_ms": 12})
    entries = _read_all(log_dir)
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "llm"
    assert e["error"] == ""
    assert e["meta"] == {"model": "test-model", "latency_ms": 12}
    assert e["input"]["api_key"] == "<masked>"
    assert e["input"]["messages"][0]["content"] == "<base64:503字符>"
    assert e["output"] == {"content": "ok"}
    assert e["ts"]   # 带时间戳（复盘定位用）


def test_record_disabled_writes_nothing(tmp_path, monkeypatch):
    """TRACE_ENABLED=0 整体关闭：不落任何文件（conftest 默认即此状态）。"""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "logs"))
    trace.record("llm", input={"a": 1}, output={"b": 2})
    assert not os.path.exists(config.LOG_DIR)


def test_record_error_field(tmp_path, monkeypatch):
    """失败留痕：error 字符串化落盘（截断保护），调用方照常抛错。"""
    log_dir = _enable(tmp_path, monkeypatch)
    try:
        raise RuntimeError("模拟 OCR 通道失败")
    except RuntimeError as exc:
        trace.record("ocr", input={"image_base64_len": 10}, error=exc)
    e = _read_all(log_dir)[0]
    assert e["error"] == "模拟 OCR 通道失败"
    assert e["output"] is None


def test_cleanup_expired_files(tmp_path, monkeypatch):
    """按天清理：超过 TRACE_KEEP_DAYS 的旧文件在写入时被删，当日文件保留。"""
    log_dir = _enable(tmp_path, monkeypatch, keep_days=1)
    os.makedirs(log_dir, exist_ok=True)
    stale = os.path.join(log_dir, "eta-trace-2020-01-01.jsonl")
    fresh_name = "eta-trace-%s.jsonl" % trace.datetime.date.today().isoformat()
    with open(stale, "w", encoding="utf-8") as f:
        f.write("{}\n")
    trace.record("llm", input={}, output="x")   # 触发每日一次清理
    assert not os.path.exists(stale)
    assert os.path.exists(os.path.join(log_dir, fresh_name))


def test_record_never_raises_on_io_error(tmp_path, monkeypatch):
    """静默失败：LOG_DIR 指到不可写位置时不抛异常（留痕绝不阻断业务）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setattr(config, "TRACE_ENABLED", True)
    monkeypatch.setattr(config, "LOG_DIR", str(blocker / "sub" / "logs"))
    trace.record("llm", input={"a": 1}, output="x")   # 不应抛出
