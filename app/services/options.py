"""选择题选项的检查与规范化：剥字母前缀、去重、按位置重编号 A/B/C/D…。

问题背景：AI 解析/生成的选项常自带字母前缀（"A. quite"），但偶尔会输出
重复前缀（两个 A、两个 B）、缺失前缀、字母错序或内容重复的选项，直接展示
会出现"两个A、两个B"之类的显示异常。本模块提供统一工具：
  - normalize_options：解析结果（photo_check / classifier 切题）产出题目时调用，
    剥掉自带前缀 → 合并重复内容 → 按位置规范重编号，并返回修复说明供前端提示；
  - strip_option_prefix：导出渲染（export_docs）打印前剥掉选项自带前缀，
    避免与渲染器的自动编号叠加成 "A. A. xxx"。

约定：选项在系统内的规范形态是带规范字母前缀（"A. text"）——错误表、练习、
试卷与解析结果的历史数据均为此形态，展示层直接展示即正确。
"""
import re
import unicodedata
from typing import Any, List, Tuple

# 最多 8 个选项（超出按数字编号，与导出渲染一致）
_LETTERS = "ABCDEFGH"

# 选项行首的字母前缀：A. / A、 / A) / (A) / A: 等。字母后必须跟标点或括号，
# 裸空格不视为前缀（避免把 "A day..." 这类以冠词开头的选项内容误剥）
_OPT_PREFIX_RE = re.compile(r"^\s*(?:[\(（]\s*)?([A-Ha-h])\s*[.、．:：\)）]\s*")

_PUNCT_SPACE_RE = re.compile(r"[\s\u3000]+")


def strip_option_prefix(text: Any) -> str:
    """剥掉选项文本行首的字母前缀（"A. quiet" → "quiet"），无前缀原样返回。"""
    s = unicodedata.normalize("NFKC", str(text or "")).strip()
    m = _OPT_PREFIX_RE.match(s)
    if not m:
        return s
    rest = s[m.end():].strip()
    return rest if rest else s   # 剥掉前缀后为空（如选项只有 "A."）→ 保留原样


def _content_key(text: str) -> str:
    """选项内容归一化键（去全半角差异与空白），用于重复判定。"""
    return _PUNCT_SPACE_RE.sub("", unicodedata.normalize("NFKC", text).lower())


def normalize_options(options: List[Any]) -> Tuple[List[str], List[str]]:
    """选项检查与规范化：剥前缀 → 去重 → 按位置重编号 A/B/C/D…。

    返回 (规范选项列表, 修复说明列表)。规范列表每项带规范字母前缀
    （"A. text"）；修复说明（中文短句）供前端提示老师哪里被自动修正过，
    无异常时为空列表。
    """
    opts = [str(o or "").strip() for o in (options or [])]
    opts = [o for o in opts if o]
    if not opts:
        return [], []
    has_prefix = any(_OPT_PREFIX_RE.match(o) for o in opts)

    issues: List[str] = []
    seen = {}          # 归一化内容 → 已用字母（重复内容合并）
    out: List[str] = []
    for o in opts:
        m = _OPT_PREFIX_RE.match(o)
        letter = m.group(1).upper() if m else ""
        rest = (o[m.end():].strip() if m else o)
        if not rest:
            issues.append("选项「%s」缺少内容，已跳过" % o[:20])
            continue
        key = _content_key(rest)
        if key in seen:
            issues.append("选项内容重复（与 %s 相同），已合并" % seen[key])
            continue
        idx = len(out)
        want = _LETTERS[idx] if idx < len(_LETTERS) else str(idx + 1)
        if letter and letter != want:
            issues.append("选项字母异常：第 %d 项是 %s，已规范为 %s" % (idx + 1, letter, want))
        elif not letter and has_prefix:
            # 同题其他选项带前缀而本项缺失——混合形态才是异常；
            # 全都不带前缀只是格式差异，静默补齐，不算异常
            issues.append("选项缺少字母：第 %d 项，已补为 %s" % (idx + 1, want))
        out.append("%s. %s" % (want, rest))
        seen[key] = want
    return out, issues
