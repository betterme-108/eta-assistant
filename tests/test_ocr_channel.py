# -*- coding: utf-8 -*-
"""OCR 通道防回归测试（不依赖网络）。

背景：_mineru_once 引用模块级 base64，曾因缺失 import 在真实使用时抛
NameError("name 'base64' is not defined")——单元测试因 mock 掉 OCR 而漏网，
此处用假 httpx 客户端走真实函数体，确保此类"用而未导入"错误在测试期暴露。
"""
from app.providers import ocr


class _FakeResp:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"code": 0, "data": {"batch_id": "b1", "file_urls": ["http://fake/url"]}}


class _FakeClient:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return _FakeResp()

    def put(self, *args, **kwargs):
        return _FakeResp()


def test_mineru_once_walks_base64_decode(monkeypatch):
    """_mineru_once 完整执行到上传步骤（覆盖 base64.b64decode 调用）。"""
    monkeypatch.setattr(ocr.httpx, "Client", _FakeClient)
    monkeypatch.setattr(ocr.time, "sleep", lambda s: None)
    monkeypatch.setattr(ocr, "_poll_mineru_batch", lambda *a, **k: "识别文本")
    text = ocr._mineru_once("aGVsbG8=")  # 修复前：NameError: name 'base64' is not defined
    assert text == "识别文本"


def test_sanitize_transcript_strips_layout_noise():
    """转录清洗：占位符/LaTeX 包裹/批改符号剥离，正文与手写填入内容保留。"""
    t = ("## √ 阅读理解。(建议用时:8 分钟)\n\n"
         "![](images/61dc89.jpg)\n\n"
         "1. How does the writer begin the passage?\n\n\n\n"
         '<div style="text-align: center;"><img src="imgs/a.jpg" /></div>\n'
         "2. The words have $ \\underline{\\text{greatly}} $ (great) influenced me.\n"
         "×× √\n")
    out = ocr._sanitize_transcript(t)
    assert "![" not in out and "<img" not in out and "<div" not in out
    assert "√" not in out and "×" not in out
    assert "greatly (great)" in out        # LaTeX 划线展平：内容（手写填入词）保留
    assert "阅读理解" in out                # 符号从标题剥离，标题本身保留
    assert "\n\n\n" not in out           # 连续空行折叠为一段
