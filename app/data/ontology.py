"""错因本体库 v3（36 类）：加载、校验、prompt 渲染、E 类结构化规则求值。

校验规则（test_ontology.py 保证）：
  - 恰好 36 类，编号唯一，组内编号连续（A01-A06 / B01-B09 / C01-C08 / D01-D08 / E01-E05）
  - 每类 signals / examples / teaching_point 非空
  - E 类必须有结构化 rule（all_of/any_of）与 contrast_with
  - C / D 类 examples 必须含中英文对照（英文错误 + 中文说明）
"""
import ast
import re
from typing import Any, Dict, List, Optional

import yaml

from ..core import config

_CACHE: Optional[Dict[str, Any]] = None

EXPECTED_COUNTS = {"A": 6, "B": 9, "C": 8, "D": 8, "E": 5}


def load_ontology(refresh: bool = False) -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    with open(config.ONTOLOGY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data = _validate(data)
    _CACHE = data
    return data


def _validate(data: Dict[str, Any]) -> Dict[str, Any]:
    groups = data.get("groups") or []
    categories = data.get("categories") or []
    group_ids = [g.get("id") for g in groups]
    assert len(categories) == 36, "本体库必须是 36 类，当前 %d 类" % len(categories)

    seen = set()
    by_group: Dict[str, int] = {}
    for cat in categories:
        cid = cat.get("id")
        assert cid and cid not in seen, "类别编号缺失或重复: %r" % cid
        seen.add(cid)
        gid = cat.get("group")
        assert gid in group_ids, "%s 的组 %r 不存在" % (cid, gid)
        for field in ("name", "signals", "examples", "teaching_point"):
            assert cat.get(field), "%s 缺少必填字段 %s" % (cid, field)
        assert isinstance(cat["signals"], list) and cat["signals"], "%s signals 不能为空" % cid
        assert isinstance(cat["examples"], list) and cat["examples"], "%s examples 不能为空" % cid
        # 组内编号连续：A01...A06 / B01...B09 ...
        m = re.match(r"^([A-E])(\d{2})$", cid)
        assert m, "编号格式应为字母+两位数字: %s" % cid
        g, n = m.group(1), int(m.group(2))
        by_group[g] = by_group.get(g, 0) + 1
        assert n == by_group[g], "%s 组内编号应连续（%s%02d）" % (cid, g, by_group[g])
        # E 类结构化规则
        if g == "E":
            rule = cat.get("rule")
            assert isinstance(rule, dict) and (
                "all_of" in rule or "any_of" in rule
            ), "%s 必须有 all_of/any_of 结构化 rule" % cid
            # E02/E05 是"粗心 vs 未掌握"对照组，必须互相引用
            if cid in ("E02", "E05"):
                pair = "E05" if cid == "E02" else "E02"
                assert cat.get("contrast_with") == pair, \
                    "%s 的 contrast_with 必须是 %s" % (cid, pair)
            # 每条规则表达式必须可解析
            for expr in _rule_exprs(rule):
                parse_rule_expr(expr)
        # C / D 类中英文对照示例
        if g in ("C", "D"):
            has_en = any(
                ex.get("wrong") or ex.get("wrong_option") for ex in cat["examples"]
            )
            has_cn = any(
                ex.get("note") or ex.get("explanation") or
                re.search(r"[\u4e00-\u9fff]", str(ex)) for ex in cat["examples"]
            )
            assert has_en, "%s 示例必须含英文错误样例（wrong/wrong_option）" % cid
            assert has_cn, "%s 示例必须含中文说明（note/explanation）" % cid

    for gid, cnt in EXPECTED_COUNTS.items():
        assert by_group.get(gid) == cnt, "组 %s 应有 %d 类，当前 %d 类" % (gid, cnt, by_group.get(gid, 0))
    return data


def _rule_exprs(rule: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    out.extend(rule.get("all_of") or [])
    out.extend(rule.get("any_of") or [])
    return out


# ---------------------------------------------------------------- 查询接口

def get_category(category_id: str) -> Optional[Dict[str, Any]]:
    for cat in load_ontology()["categories"]:
        if cat["id"] == category_id:
            return cat
    return None


def category_name(category_id: str) -> str:
    """类别显示名（不含编号，编号仅内部溯源用）。"""
    cat = get_category(category_id)
    return cat["name"] if cat else category_id


def all_categories() -> List[Dict[str, Any]]:
    return load_ontology()["categories"]



# ---------------------------------------------------------------- prompt 渲染

def render_prompt_text() -> str:
    """渲染为注入大模型 prompt 的树形文本：组 → 类 → 信号/示例/教学要点。"""
    data = load_ontology()
    lines = ["【错因本体库 v3 · 36 类】（category_id 必须严格取自下表，禁止自创）"]
    for g in data["groups"]:
        lines.append("")
        lines.append("◆ %s %s（%s）" % (g["id"], g["name"], g.get("name_en", "")))
        for cat in data["categories"]:
            if cat["group"] != g["id"]:
                continue
            lines.append("  - %s %s / %s" % (cat["id"], cat["name"], cat.get("name_en", "")))
            for s in cat["signals"][:3]:
                lines.append("    信号: %s" % s)
            for ex in cat["examples"][:2]:
                en = ex.get("wrong") or ex.get("wrong_option") or ex.get("scenario") or ""
                cn = ex.get("note") or ex.get("explanation") or ex.get("judgement") or ""
                if en:
                    lines.append("    例: %s | %s" % (en, cn))
            lines.append("    要点: %s" % cat["teaching_point"])
    return "\n".join(lines)


# ---------------------------------------------------------------- E 类规则求值

_ALLOWED_NODES = (
    ast.Expression, ast.Compare, ast.Constant, ast.Load,
    ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.And, ast.Or,
    ast.BoolOp, ast.Name,
)

_RULE_VARIABLES = {
    "student_cat_count", "class_correct_rate", "student_other_similar_correct",
    "answer_is_blank", "self_contradiction_found", "task_requirement_misread",
}


def parse_rule_expr(expr: str) -> ast.Expression:
    """解析形如 'student_cat_count >= 3' 的受限于比较表达式的 AST。"""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError("规则表达式含不支持的语法: %r (%s)" % (expr, type(node).__name__))
    return tree


def eval_rule_expr(expr: str, variables: Dict[str, Any]) -> Any:
    """在给定变量下安全求值规则表达式（仅支持 ==/!=/>/>=/</<= 与 and/or）。"""
    tree = parse_rule_expr(expr)
    return _eval_node(tree.body, variables)


def _eval_node(node: ast.AST, variables: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, variables)
            result = result and _compare(op, left, right)
            left = right
        return result
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, variables) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        # YAML 规则习惯用小写 true/false 作布尔字面量（Python 语法中是 Name）
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id not in _RULE_VARIABLES:
            raise ValueError("规则中使用了未知变量: %s（允许: %s）"
                             % (node.id, ", ".join(sorted(_RULE_VARIABLES))))
        return variables[node.id]
    raise ValueError("不支持的节点: %s" % type(node).__name__)


def _compare(op: ast.AST, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    raise ValueError("不支持的比较运算: %s" % type(op).__name__)


def eval_e_rule(category_id: str, variables: Dict[str, Any]) -> bool:
    """对 E 类别按其结构化 rule 求值。

    变量约定（由调用方注入）：
      student_cat_count          该生该错因历史出现次数（教师确认口径）
      class_correct_rate         该题班级正确率（0-1）
      student_other_similar_correct  该生其他同类题是否正确
    """
    cat = get_category(category_id)
    if not cat or "rule" not in cat:
        raise ValueError("%s 不是 E 类或缺少 rule" % category_id)
    rule = cat["rule"]
    if "all_of" in rule:
        return all(eval_rule_expr(e, variables) for e in rule["all_of"])
    return any(eval_rule_expr(e, variables) for e in rule["any_of"])
