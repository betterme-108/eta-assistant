"""选项检查与规范化：重复字母（两个A）、内容重复、字母缺失/错序的修复，
以及导出渲染不出现 "A. A. xxx" 双字母的回归验证。"""
from app.services import export_docs, options


# ---------------------------------------------------------------- 剥前缀

def test_strip_option_prefix_basic():
    assert options.strip_option_prefix("A. quiet") == "quiet"
    assert options.strip_option_prefix("B、quiet") == "quiet"
    assert options.strip_option_prefix("C) quiet") == "quiet"
    assert options.strip_option_prefix("(D) quiet") == "quiet"
    assert options.strip_option_prefix("Ａ．quiet") == "quiet"   # 全角归一
    assert options.strip_option_prefix("quiet") == "quiet"       # 无前缀原样


def test_strip_option_prefix_keeps_articles():
    """以冠词开头的选项内容不得被误剥（裸空格不视为前缀）。"""
    assert options.strip_option_prefix("A day with no homework is boring.") == \
        "A day with no homework is boring."
    assert options.strip_option_prefix("I usually go to school by bus.") == \
        "I usually go to school by bus."


def test_strip_option_prefix_letter_only():
    """选项只有字母前缀时剥空 → 保留原样，不丢内容。"""
    assert options.strip_option_prefix("A.") == "A."


# ---------------------------------------------------------------- 规范化检查

def test_normalize_options_no_anomaly_silent():
    opts, issues = options.normalize_options(
        ["A. quiet", "B. quite", "C. quit", "D. quilt"])
    assert opts == ["A. quiet", "B. quite", "C. quit", "D. quilt"]
    assert issues == []


def test_normalize_options_duplicate_letters():
    """两个 A、两个 B（LLM 偶尔输出重复前缀）→ 按位置规范重编号。"""
    opts, issues = options.normalize_options(
        ["A. quiet", "A. quite", "B. quit", "B. quilt"])
    assert opts == ["A. quiet", "B. quite", "C. quit", "D. quilt"]
    assert any("第 2 项是 A" in i for i in issues)
    assert any("第 3 项是 B" in i for i in issues)


def test_normalize_options_duplicate_content():
    """内容重复的选项合并，后面的顺位前移。"""
    opts, issues = options.normalize_options(
        ["A. quiet", "B. quiet", "C. quit"])
    assert opts == ["A. quiet", "B. quit"]
    assert any("内容重复" in i for i in issues)


def test_normalize_options_missing_letter_mixed():
    """同题部分选项缺前缀 → 补齐并提示（混合形态才是异常）。"""
    opts, issues = options.normalize_options(["A. quiet", "quite", "quit"])
    assert opts == ["A. quiet", "B. quite", "C. quit"]
    assert any("缺少字母" in i for i in issues)


def test_normalize_options_no_prefix_at_all_silent():
    """全部选项都没有前缀只是格式差异：静默补编号，不算异常。"""
    opts, issues = options.normalize_options(["quiet", "quite", "quit"])
    assert opts == ["A. quiet", "B. quite", "C. quit"]
    assert issues == []


def test_normalize_options_wrong_order():
    """字母错序（A、C、B）→ 重编号并提示。"""
    opts, issues = options.normalize_options(["A. quiet", "C. quit", "B. quite"])
    assert opts == ["A. quiet", "B. quit", "C. quite"]
    assert any("第 3 项是 B" in i for i in issues)


def test_normalize_options_empty():
    assert options.normalize_options([]) == ([], [])
    assert options.normalize_options(None) == ([], [])


# ---------------------------------------------------------------- 导出渲染双字母回归

def test_render_html_no_double_letter():
    """选项自带前缀的历史数据渲染时剥前缀，不出现 "A. A. xxx"。"""
    meta = export_docs.paper_doc({
        "title": "综合测试卷", "items": [
            {"type": "单选", "q": "Choose the correct answer.",
             "options": ["A. quiet", "B. quite", "C. quit", "D. quilt"],
             "answer": "B", "explanation": "quiet 意为安静的"},
        ]})
    html = export_docs.render_html(meta, with_answers=True)
    assert "A. quiet</p>" in html
    assert "A. A. " not in html
    assert "B. B. " not in html
    assert "【答案】B" in html
    assert "【解析】" in html


def test_render_html_student_version_hides_answers():
    """学生卷只有题目没有答案与解析。"""
    meta = export_docs.paper_doc({
        "title": "综合测试卷", "items": [
            {"type": "单选", "q": "Choose the correct answer.",
             "options": ["A. quiet", "B. quite"], "answer": "B",
             "explanation": "quiet 意为安静的"},
            {"type": "改错", "q": "改正句子。", "options": [],
             "answer": "I saw the film yesterday.", "explanation": "过去时"},
        ]})
    html = export_docs.render_html(meta, with_answers=False)
    assert "【答案】" not in html
    assert "【解析】" not in html
    assert "quiet 意为安静的" not in html
    assert "答：____________________________" in html   # 非选择题留作答空位


def test_render_student_report_html_assignment():
    """单生报告：成绩摘要、逐题明细（含作答与判定）与 AI 总结建议。"""
    session = {"id": 1, "kind": "assignment", "title": "第五单元小测",
               "class_name": "一班", "meta": {"class_name": "一班"}}
    result = {
        "student_code": "S01", "correct_n": 1, "judged_n": 2,
        "uncertain_n": 0, "score": 50.0,
        "items": [
            {"no": 1, "qtype": "单选", "question": "Choose.",
             "options": ["A. go", "B. went"], "answer": "A", "correct": "B",
             "score": 2, "got": 0, "verdict": "wrong", "note": "与标准答案不符",
             "rubric": None, "passage": ""},
            {"no": 2, "qtype": "完成句子", "question": "Fill.",
             "options": [], "answer": "am", "correct": "am", "score": 2,
             "got": 2, "verdict": "correct", "note": "", "rubric": None,
             "passage": ""},
        ]}
    html = export_docs.render_student_report_html(
        session, result, "张三", {"summary": "个别错误", "advice": "加强练习"})
    assert "批改报告" in html
    assert "张三" in html and "S01" in html
    assert "50.0" in html
    assert "A. go" in html            # 选项展示（自带规范前缀）
    assert "错" in html and "对" in html
    assert "个别错误" in html and "加强练习" in html
    assert "不用于排名" in html


def test_render_student_report_html_dictation():
    """听写形态：按词展示英文/中文/作答/判定。"""
    session = {"id": 2, "kind": "dictation", "title": "听写",
               "class_name": "一班", "meta": {}}
    result = {
        "student_code": "S01", "correct_n": 1, "total": 2,
        "uncertain_n": 0, "score": 50.0,
        "items": [
            {"no": 1, "en": "apple", "zh": "苹果", "answer": "apple",
             "verdict": "correct", "note": ""},
            {"no": 2, "en": "banana", "zh": "香蕉", "answer": "bannana",
             "verdict": "misspell", "note": "拼写有误"},
        ]}
    html = export_docs.render_student_report_html(
        session, result, "", {"summary": "", "advice": ""})
    assert "apple" in html and "苹果" in html
    assert "bannana" in html
    assert "拼写错" in html
    assert "总结" not in html          # AI 失败时空串 → 不渲染空总结框
