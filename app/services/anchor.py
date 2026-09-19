# -*- coding: utf-8 -*-
"""坐标锚点：手写作答行的题号归属（题答关联的兜底路径）。

作答流直读出的手写作答带不出题号时（no=0，如写在空白处/题侧的句子），
用文档流行坐标就近归属：手写文本先在转录行里找到对应行（归一化包含
匹配），该行归属垂直距离最近的上方题号锚点。行坐标来自 qwen_vl_ocr
advanced_recognition（ocr_page_with_boxes 的 lines）；无坐标（其他通道/
未配置）时整体不启用，题号仍由作答流直读或转录猜测承担。

行级坐标的固有限制：一行含多题（"(A)7. … (B)8. …"）只取行首题号作
锚点，该行上的作答会误归属前一题——但此场景题号直读通常成功，锚点
只在直读失败时兜底（confidence=medium，前端可见可改）。
"""
import re
from typing import Any, Dict, List, Optional

# 题号锚点行：行首可带作答括号（"(A) 1."、"( ) 2."、"(17)3."——括号内是
# 误读成数字的选项字母），核心是 序号 + 分隔符。大题标题（"IV 完形填空"，
# 罗马数字后无数字）、选项行（"C. Unless D. Because"）、正文挖空（"8 the
# way…"，数字后无分隔符）都不匹配
_NO_ANCHOR_RE = re.compile(r"^(?:\(\s*[0-9A-Da-d]{0,2}\s*\))?[\s.、．]*(\d{1,2})\s*[.、．]")

# 手写行归属题号锚点的最大垂直距离（平均行高的倍数）：超过视为脱离题区
_NEAR_BAND = 2.5

# 归一化比对：去空白/英文标点、小写（OCR 噪声与手写转写的表层差异容忍）
_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def detect_anchors(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转录行 → 题号锚点列表（[{no, cy, h, bottom}]，按 cy 升序）。

    box 为旋转矩形中心坐标 [cx, cy, w, h]（角度不影响垂直归属，忽略）。
    """
    anchors: List[Dict[str, Any]] = []
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        m = _NO_ANCHOR_RE.match(str(ln.get("text") or "").strip())
        box = ln.get("box")
        if not m or not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        try:
            cy, h = float(box[1]), float(box[3])
            anchors.append({"no": int(m.group(1)), "cy": cy,
                            "h": h, "bottom": cy + h / 2.0})
        except (TypeError, ValueError):
            continue
    anchors.sort(key=lambda a: a["cy"])
    return anchors


def _norm_text(t: str) -> str:
    return _NORM_RE.sub("", (t or "").lower())


def find_line(text: str, lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """手写文本 → 转录中对应的行（归一化包含匹配）。

    手写句是转录行的子串；多个行命中时取超出量最小（内容最贴近手写
    文本）的那行；无命中返回 None（宁缺勿错，不挂题号）。
    """
    needle = _norm_text(text)
    if not needle:
        return None
    best: Optional[Dict[str, Any]] = None
    best_diff = -1
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        hay = _norm_text(str(ln.get("text") or ""))
        if needle in hay:
            diff = len(hay) - len(needle)
            if best is None or diff < best_diff:
                best, best_diff = ln, diff
    return best


def nearest_no(line: Dict[str, Any], anchors: List[Dict[str, Any]]) -> int:
    """行 → 最近上方题号锚点的题号（超出带宽返回 0）。

    手写作答通常写在题号下方、下一题上方之间；锚点允许有半行重叠
    （作答写在题号右侧括号里时同行）。距离超 _NEAR_BAND 倍平均行高
    视为脱离题区（页面底部的作答区/教师评语不误挂）。
    """
    if not anchors or not isinstance(line, dict):
        return 0
    box = line.get("box")
    if not isinstance(box, (list, tuple)) or len(box) < 3:
        return 0
    try:
        cy = float(box[1])
    except (TypeError, ValueError):
        return 0
    avg_h = sum(a["h"] for a in anchors) / len(anchors) or 1.0
    best: Optional[Dict[str, Any]] = None
    best_gap: Optional[float] = None
    for a in anchors:
        gap = cy - a["bottom"]
        if gap < -avg_h / 2.0:   # 在锚点上方超过半行：不属于该锚点
            continue
        if best is None or gap < best_gap:
            best, best_gap = a, gap
    if best is None or best_gap > _NEAR_BAND * avg_h:
        return 0
    return best["no"]
