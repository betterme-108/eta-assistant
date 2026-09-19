"""本体库 v3 完整性测试：36 类、编号唯一、字段齐全、E 类规则、C/D 中英文对照。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ontology


def setup_module(module):
    ontology.load_ontology(refresh=True)


def test_exactly_36_categories():
    cats = ontology.all_categories()
    assert len(cats) == 36


def test_ids_unique_and_well_formed():
    ids = [c["id"] for c in ontology.all_categories()]
    assert len(set(ids)) == 36
    for cid in ids:
        assert len(cid) == 3 and cid[0] in "ABCDE" and cid[1:].isdigit(), cid


def test_group_counts():
    counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for c in ontology.all_categories():
        counts[c["group"]] += 1
    assert counts == {"A": 6, "B": 9, "C": 8, "D": 8, "E": 5}


def test_required_fields_present():
    for c in ontology.all_categories():
        assert c["name"]
        assert c["signals"] and isinstance(c["signals"], list)
        assert c["examples"] and isinstance(c["examples"], list)
        assert c["teaching_point"]
        assert c["name_en"]


def test_e_categories_have_structured_rules():
    e_cats = [c for c in ontology.all_categories() if c["group"] == "E"]
    assert len(e_cats) == 5
    for c in e_cats:
        assert isinstance(c.get("rule"), dict)
        assert "all_of" in c["rule"] or "any_of" in c["rule"]
    # E02/E05 是"粗心 vs 未掌握"对照组，必须互相引用
    e02 = ontology.get_category("E02")
    e05 = ontology.get_category("E05")
    assert e02["contrast_with"] == "E05"
    assert e05["contrast_with"] == "E02"


def test_e_rule_engine():
    # E02 粗心：需同时满足三个条件
    vars_careless = {
        "student_cat_count": 1,
        "class_correct_rate": 0.8,
        "student_other_similar_correct": True,
    }
    assert ontology.eval_e_rule("E02", vars_careless) is True
    vars_not_careless = dict(vars_careless, class_correct_rate=0.5)
    assert ontology.eval_e_rule("E02", vars_not_careless) is False

    # E05 未掌握：任一条件成立即可
    assert ontology.eval_e_rule(
        "E05", {"student_cat_count": 4, "class_correct_rate": 0.9}) is True
    assert ontology.eval_e_rule(
        "E05", {"student_cat_count": 1, "class_correct_rate": 0.4}) is True
    assert ontology.eval_e_rule(
        "E05", {"student_cat_count": 1, "class_correct_rate": 0.9}) is False

    # E01 审题偏差：任务要求误读信号成立即可
    assert ontology.eval_e_rule("E01", {"task_requirement_misread": True}) is True
    assert ontology.eval_e_rule("E01", {"task_requirement_misread": False}) is False

    # E04 空白未答：作答为空
    assert ontology.eval_e_rule("E04", {"answer_is_blank": True}) is True
    assert ontology.eval_e_rule("E04", {"answer_is_blank": False}) is False


def test_rule_expr_security():
    # 危险表达式必须被拒绝
    for evil in ("__import__('os').system('ls')", "student_cat_count + 1", "x[0]"):
        try:
            ontology.parse_rule_expr(evil)
        except (ValueError, SyntaxError):
            pass
        else:
            raise AssertionError("危险表达式未被拒绝: %r" % evil)


def test_c_d_categories_have_bilingual_examples():
    for c in ontology.all_categories():
        if c["group"] in ("C", "D"):
            has_en = any(ex.get("wrong") or ex.get("wrong_option") for ex in c["examples"])
            has_cn = any(
                ex.get("note") or ex.get("explanation") for ex in c["examples"])
            assert has_en, "%s 需英文错误样例" % c["id"]
            assert has_cn, "%s 需中文说明" % c["id"]


def test_get_category_and_name():
    cat = ontology.get_category("C03")
    assert cat is not None and cat["name"] == "推理判断越界"
    assert "推理判断越界" in ontology.category_name("C03")
    assert ontology.get_category("Z99") is None


def test_prompt_text_renders_all_groups():
    text = ontology.render_prompt_text()
    for gid, name in (("A", "词汇知识类"), ("B", "语法结构类"),
                      ("C", "语篇理解类"), ("D", "书面表达类"), ("E", "答题行为类")):
        assert name in text
    assert "C03" in text and "推理判断越界" in text
    assert "禁止自创" in text
