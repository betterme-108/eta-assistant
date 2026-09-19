"""题目类导出：可打印网页（HTML）与 Word 文档；学生版纯题目、教师版含答案与解析。

服务练习导出（单套或多套合并）与试卷导出（AI 组卷结果）：
  - HTML：浏览器打开即可打印/另存 PDF（零依赖），@page 适配 A4；
  - Word：python-docx 生成 .docx，教师可在 Word/WPS 里继续编辑；
  - 教师版（with_answers）在每题后附答案与解析，学生版只留作答空间。
导出内容只含题目本身，不含任何学生数据（合规边界与练习页一致）。
"""
import io
from datetime import date
from typing import Any, Dict, List

from . import options as options_mod

_LETTERS = "ABCDEFGH"


def _norm_items(items: List[Dict[str, Any]],
                category_name: str = "") -> List[Dict[str, Any]]:
    """统一题面字段：type/q/options/answer/explanation/passage + 分组名。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("q"):
            continue
        out.append({
            "type": it.get("type") or "题",
            "q": str(it["q"]),
            "options": [str(o) for o in (it.get("options") or [])],
            "answer": str(it.get("answer") or ""),
            "explanation": str(it.get("explanation") or ""),
            "passage": str(it.get("passage") or ""),
            "category_name": category_name,
        })
    return out


def practices_doc(practices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一套或多套练习合并为一个可导出文档：按类别分组、全局重编号。"""
    sections = []
    for p in practices:
        cname = p.get("category_name") or ""   # 生成接口返回；列表里只有 category_id
        if not cname:
            from ..data import ontology
            cname = ontology.category_name(p.get("category_id") or "")
        items = _norm_items(p.get("items"), cname)
        if items:
            sections.append({"name": cname, "items": items})
    cls = next((p.get("class_name") for p in practices if p.get("class_name")), "")
    title = " · ".join(s["name"] for s in sections[:3]) + ("等" if len(sections) > 3 else "")
    return {
        "title": "配套练习：%s" % title if sections else "配套练习",
        "class_name": cls or "",
        "sub": "按错因分组 · 共 %d 题" % sum(len(s["items"]) for s in sections),
        "sections": sections,
        "flat": [it for s in sections for it in s["items"]],
    }


def paper_doc(paper: Dict[str, Any]) -> Dict[str, Any]:
    """试卷（AI 组卷）→ 可导出文档：单分组、按题号顺序。"""
    items = _norm_items(paper.get("items"), "")
    scope = paper.get("scope") or ""
    return {
        "title": paper.get("title") or "综合测试卷",
        "class_name": paper.get("class_name") or "",
        "sub": " · ".join(x for x in (scope, "共 %d 题" % len(items)) if x),
        "sections": [{"name": "", "items": items}],
        "flat": items,
    }


def sheet_doc(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """讲评方案（决策单）→ 可导出文档结构：三段式 + 典型错例 + 合规提示语。"""
    focus = []
    for f in sheet.get("focus") or []:
        focus.append({
            "category_id": f.get("category_id", ""),
            "name": f.get("name", ""),
            "count": f.get("count", 0),
            "rate": f.get("rate", 0),
            "teaching_point": f.get("teaching_point", ""),
            "practice_hint": f.get("practice_hint", ""),
            "examples": [{
                "qtype": ex.get("qtype", ""),
                "question": ex.get("question", ""),
                "answer": ex.get("answer", ""),
                "evidence": ex.get("evidence", ""),
            } for ex in (f.get("examples") or [])],
        })
    return {
        "class_name": sheet.get("class_name") or "",
        "exam_type": sheet.get("exam_type") or "",
        "class_size": sheet.get("class_size", 0),
        "total_confirmed": sheet.get("total_confirmed", 0),
        "sheet_id": sheet.get("sheet_id"),
        "focus": focus,
        "skip": [{"category_id": k.get("category_id", ""), "name": k.get("name", ""),
                  "count": k.get("count", 0), "advice": k.get("advice", "")}
                 for k in (sheet.get("skip") or [])],
        "tutors": [{"student_code": t.get("student_code", ""),
                    "category_id": t.get("category_id", ""), "name": t.get("name", ""),
                    "count": t.get("count", 0), "advice": t.get("advice", "")}
                   for t in (sheet.get("tutors") or [])],
        "notice": sheet.get("notice", ""),
    }


# ---------------------------------------------------------------- HTML（可打印网页）

def render_html(meta: Dict[str, Any], with_answers: bool) -> str:
    """A4 打印友好的网页；教师版每题附答案与解析，学生版留作答空间。"""
    ver = "教师版 · 含答案与解析" if with_answers else "学生卷"
    head = ("<div class='head'><h1>%s</h1><p>%s · %s · %s</p></div>"
            % (_esc(meta["title"]), _esc(meta["sub"]), _esc(meta["class_name"] or "全班级"),
               date.today().strftime("%Y 年 %m 月 %d 日")))

    body = []
    qno = 0
    for sec in meta["sections"]:
        if sec["name"]:
            body.append("<h2 class='sec'>%s</h2>" % _esc(sec["name"]))
        for it in sec["items"]:
            qno += 1
            body.append("<div class='q'>")
            body.append("<p class='stem'><b>%d.</b> <span class='t'>[%s]</span> %s</p>"
                        % (qno, _esc(it["type"]), _esc(it["q"])))
            if it["passage"]:
                body.append("<pre class='passage'>%s</pre>" % _esc(it["passage"]))
            for j, opt in enumerate(it["options"]):
                # 剥掉选项自带的字母前缀再编号，避免渲染成 "A. A. xxx"（两个A）
                body.append("<p class='opt'>%s. %s</p>"
                            % (_LETTERS[j] if j < len(_LETTERS) else str(j + 1),
                               _esc(options_mod.strip_option_prefix(opt))))
            if with_answers:
                if it["answer"]:
                    body.append("<p class='ans'>【答案】%s</p>" % _esc(it["answer"]))
                if it["explanation"]:
                    body.append("<p class='exp'>【解析】%s</p>" % _esc(it["explanation"]))
            elif not it["options"]:
                body.append("<p class='blank'>答：____________________________</p>")
            body.append("</div>")

    return """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>%(title)s（%(ver)s）</title>
<style>
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Songti SC", "SimSun", "Noto Serif SC", serif;
  color: #111; max-width: 178mm; margin: 0 auto; padding: 14mm 4mm; line-height: 1.9; }
.noprint { position: fixed; right: 14px; top: 14px; }
@media print { .noprint { display: none; } }
.head { text-align: center; border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 14px; }
.head h1 { font-size: 20px; margin: 0 0 4px; }
.head p { font-size: 12.5px; color: #333; margin: 0; }
h2.sec { font-size: 15px; margin: 16px 0 6px; padding-left: 8px; border-left: 4px solid #444; }
.q { margin: 10px 0; page-break-inside: avoid; }
.stem { margin: 0; font-size: 14px; }
.stem .t { font-size: 12px; color: #555; }
.opt { margin: 2px 0 2px 2em; font-size: 13.5px; }
.passage { white-space: pre-wrap; font-family: inherit; background: #f6f6f6;
  border: 1px solid #ddd; padding: 8px 10px; font-size: 13px; margin: 6px 0; }
.ans { margin: 4px 0 0; font-size: 13.5px; color: #b00; font-weight: 700; }
.exp { margin: 2px 0 0; font-size: 12.5px; color: #555; }
.blank { margin: 10px 0 2px; color: #999; }
.foot { margin-top: 20px; border-top: 1px solid #999; padding-top: 6px;
  font-size: 11px; color: #777; text-align: center; }
</style></head><body>
<button class="noprint" onclick="window.print()">🖨 打印 / 另存 PDF</button>
%(head)s
%(body)s
<div class="foot">%(ver)s · 本卷由英语教学助手按班级错题生成，仅供教学使用，不得用于学生排名或评价。</div>
</body></html>""" % {"title": _esc(meta["title"]), "ver": ver, "head": head,
                     "body": "\n".join(body)}


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------- 讲评方案（决策单）导出

def render_sheet_html(sheet: Dict[str, Any]) -> str:
    """讲评方案打印友好网页（A4）：三段式排版、错例灰底、名单表格化。"""
    meta = sheet_doc(sheet)
    parts = []
    parts.append(
        "<div class='head'><h1>英语讲评课方案</h1>"
        "<p>班级：%s · 班级人数：%s · 确认错题：%s%s · %s</p></div>" % (
            _esc(meta["class_name"] or "全部班级"), meta["class_size"],
            meta["total_confirmed"],
            (" · 任务类型：" + _esc(meta["exam_type"])) if meta["exam_type"] else "",
            date.today().strftime("%Y 年 %m 月 %d 日")))

    # ① 本课重点讲
    parts.append("<h2 class='sec'>一、本课重点讲（%d 类）</h2>" % len(meta["focus"]))
    if not meta["focus"]:
        parts.append("<p class='muted'>没有错得足够多的错因（需 ≥3 人次）。</p>")
    for f in meta["focus"]:
        parts.append("<div class='fc'><div class='fc-t'>"
                     "<span class='cat'>%s</span><b>%s</b>"
                     "<span class='num'>%d 人次 · %.0f%%</span></div>" % (
                         _esc(f["category_id"]), _esc(f["name"]),
                         f["count"], min(100, round(f["rate"] * 100))))
        if f["teaching_point"]:
            parts.append("<p class='fc-p'><b>教学要点：</b>%s</p>"
                         % _esc(f["teaching_point"]))
        if f["practice_hint"]:
            parts.append("<p class='fc-p'><b>练习：</b>%s</p>" % _esc(f["practice_hint"]))
        for ex in f["examples"]:
            parts.append("<div class='ex'>"
                         + ("<span class='qt'>[%s]</span>" % _esc(ex["qtype"])
                            if ex["qtype"] else "")
                         + "<div class='ex-q'>%s</div>" % _esc(ex["question"])
                         + ("<div class='ex-a'>错例：%s</div>" % _esc(ex["answer"])
                            if ex["answer"] else "")
                         + ("<div class='ex-s'>%s</div>" % _esc(ex["evidence"])
                            if ex["evidence"] else "")
                         + "</div>")
        parts.append("</div>")

    # ② 本课不讲清单
    parts.append("<h2 class='sec'>二、本课不讲清单（错误率 < 10%，个别反馈解决）</h2>")
    if not meta["skip"]:
        parts.append("<p class='muted'>无</p>")
    else:
        parts.append("<table class='tbl'><tr><th>错因</th><th>人次</th><th>处理建议</th></tr>")
        for k in meta["skip"]:
            parts.append("<tr><td>%s %s</td><td>%d</td><td>%s</td></tr>" % (
                _esc(k["category_id"]), _esc(k["name"]), k["count"], _esc(k["advice"])))
        parts.append("</table>")

    # ③ 个别辅导名单
    parts.append("<h2 class='sec'>三、个别辅导名单（同类错因 ≥4 次）</h2>")
    if not meta["tutors"]:
        parts.append("<p class='muted'>无</p>")
    else:
        parts.append("<table class='tbl'><tr><th>学生代号</th><th>错因</th><th>次数</th><th>辅导建议</th></tr>")
        for t in meta["tutors"]:
            parts.append("<tr><td><b>%s</b></td><td>%s %s</td><td>%d</td><td>%s</td></tr>" % (
                _esc(t["student_code"]), _esc(t["category_id"]), _esc(t["name"]),
                t["count"], _esc(t["advice"])))
        parts.append("</table>")

    if meta["notice"]:
        parts.append("<div class='foot'>%s</div>" % _esc(meta["notice"]))

    return """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>英语讲评课方案（打印版）</title>
<style>
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Songti SC", "SimSun", "Noto Serif SC", serif;
  color: #111; max-width: 182mm; margin: 0 auto; padding: 10mm 4mm; line-height: 1.75; }
.noprint { position: fixed; right: 14px; top: 14px; }
@media print { .noprint { display: none; } }
.head { text-align: center; border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 12px; }
.head h1 { font-size: 21px; margin: 0 0 4px; }
.head p { font-size: 12.5px; color: #333; margin: 0; }
h2.sec { font-size: 16px; margin: 18px 0 8px; padding-left: 8px; border-left: 4px solid #444;
  page-break-after: avoid; }
.muted { color: #777; font-size: 13px; }
.fc { border: 1px solid #ccc; border-left: 4px solid #2b6cb0; padding: 10px 14px; margin: 10px 0;
  page-break-inside: avoid; }
.fc-t { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.fc-t b { font-size: 15.5px; }
.cat { font-size: 12px; color: #2b6cb0; font-weight: 700; }
.num { margin-left: auto; font-size: 12.5px; color: #333; }
.fc-p { margin: 4px 0 0; font-size: 13px; }
.ex { background: #f6f6f6; border-radius: 4px; padding: 8px 12px; margin-top: 8px;
  font-size: 12.5px; page-break-inside: avoid; }
.ex .qt { color: #555; margin-right: 4px; }
.ex-q { font-weight: 600; }
.ex-a { color: #b00; margin-top: 2px; }
.ex-s { color: #555; margin-top: 2px; }
.tbl { width: 100%%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { border: 1px solid #bbb; padding: 5px 9px; text-align: left; vertical-align: top; }
.tbl th { background: #eee; }
.tbl tr { page-break-inside: avoid; }
.foot { margin-top: 22px; border-top: 1px solid #999; padding-top: 8px;
  font-size: 11.5px; color: #555; text-align: center; }
</style></head><body>
<button class="noprint" onclick="window.print()">🖨 打印 / 另存 PDF</button>
%s
</body></html>""" % "\n".join(parts)


def render_sheet_docx(sheet: Dict[str, Any]) -> io.BytesIO:
    """讲评方案 Word 版（内容与打印页一致）。"""
    from docx import Document
    from docx.shared import Pt, RGBColor

    meta = sheet_doc(sheet)
    doc = Document()
    doc.add_heading("英语讲评课方案", level=1)
    sub = doc.add_paragraph()
    r = sub.add_run("班级：%s · 班级人数：%s · 确认错题：%s · %s" % (
        meta["class_name"] or "全部班级", meta["class_size"], meta["total_confirmed"],
        date.today().strftime("%Y 年 %m 月 %d 日")))
    r.font.size = Pt(10.5)

    blue = RGBColor(0x2B, 0x6C, 0xB0)
    red = RGBColor(0xB0, 0x00, 0x00)
    grey = RGBColor(0x60, 0x60, 0x60)

    doc.add_heading("一、本课重点讲（%d 类）" % len(meta["focus"]), level=2)
    if not meta["focus"]:
        doc.add_paragraph("没有错得足够多的错因（需 ≥3 人次）。")
    for f in meta["focus"]:
        p = doc.add_paragraph()
        pr = p.add_run("[%s] %s　" % (f["category_id"], f["name"]))
        pr.bold = True
        pr.font.color.rgb = blue
        nr = p.add_run("%d 人次 · %.0f%%" % (f["count"], min(100, round(f["rate"] * 100))))
        nr.font.size = Pt(10)
        if f["teaching_point"]:
            tp = doc.add_paragraph()
            tr = tp.add_run("教学要点：%s" % f["teaching_point"])
            tr.font.size = Pt(10.5)
        if f["practice_hint"]:
            ph = doc.add_paragraph()
            hr = ph.add_run("练习：%s" % f["practice_hint"])
            hr.font.size = Pt(10.5)
        for ex in f["examples"]:
            ep = doc.add_paragraph()
            ep.paragraph_format.left_indent = Pt(18)
            if ex["qtype"]:
                qr = ep.add_run("[%s] " % ex["qtype"])
                qr.font.size = Pt(9.5)
                qr.font.color.rgb = grey
            er = ep.add_run(ex["question"])
            er.font.size = Pt(10.5)
            if ex["answer"]:
                ap = doc.add_paragraph()
                ap.paragraph_format.left_indent = Pt(18)
                ar = ap.add_run("错例：%s" % ex["answer"])
                ar.font.size = Pt(10.5)
                ar.font.color.rgb = red
            if ex["evidence"]:
                sp = doc.add_paragraph()
                sp.paragraph_format.left_indent = Pt(18)
                sr = sp.add_run(ex["evidence"])
                sr.font.size = Pt(10)
                sr.font.color.rgb = grey

    doc.add_heading("二、本课不讲清单（错误率 < 10%，个别反馈解决）", level=2)
    if not meta["skip"]:
        doc.add_paragraph("无")
    for k in meta["skip"]:
        kp = doc.add_paragraph()
        kr = kp.add_run("[%s] %s · %d 人次 · %s"
                        % (k["category_id"], k["name"], k["count"], k["advice"]))
        kr.font.size = Pt(10.5)

    doc.add_heading("三、个别辅导名单（同类错因 ≥4 次）", level=2)
    if not meta["tutors"]:
        doc.add_paragraph("无")
    for t in meta["tutors"]:
        tp = doc.add_paragraph()
        tr = tp.add_run("%s　" % t["student_code"])
        tr.bold = True
        ar = tp.add_run("[%s] %s · 同类错误 %d 次 · %s" % (
            t["category_id"], t["name"], t["count"], t["advice"]))
        ar.font.size = Pt(10.5)

    if meta["notice"]:
        foot = doc.add_paragraph()
        fr = foot.add_run(meta["notice"])
        fr.font.size = Pt(9)
        fr.font.color.rgb = grey

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- Word（.docx）

def render_docx(meta: Dict[str, Any], with_answers: bool) -> io.BytesIO:
    """生成 .docx（内容与 HTML 版一致）；返回 BytesIO 供流式下载。"""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading(meta["title"], level=1)
    sub = doc.add_paragraph()
    r = sub.add_run("%s · %s · %s" % (meta["sub"], meta["class_name"] or "全班级",
                                       date.today().strftime("%Y 年 %m 月 %d 日")))
    r.font.size = Pt(10.5)

    red = RGBColor(0xB0, 0x00, 0x00)
    grey = RGBColor(0x60, 0x60, 0x60)
    qno = 0
    for sec in meta["sections"]:
        if sec["name"]:
            doc.add_heading(sec["name"], level=2)
        for it in sec["items"]:
            qno += 1
            p = doc.add_paragraph()
            p.add_run("%d. " % qno).bold = True
            tr = p.add_run("[%s] " % it["type"])
            tr.font.size = Pt(10)
            tr.font.color.rgb = grey
            p.add_run(it["q"])
            if it["passage"]:
                pp = doc.add_paragraph(it["passage"])
                pp.paragraph_format.left_indent = Pt(18)
                for run in pp.runs:
                    run.font.size = Pt(10.5)
            for j, opt in enumerate(it["options"]):
                # 剥掉选项自带的字母前缀再编号，避免渲染成 "A. A. xxx"（两个A）
                po = doc.add_paragraph("%s. %s" % (
                    _LETTERS[j] if j < len(_LETTERS) else str(j + 1),
                    options_mod.strip_option_prefix(opt)))
                po.paragraph_format.left_indent = Pt(24)
            if with_answers:
                if it["answer"]:
                    pa = doc.add_paragraph()
                    ar = pa.add_run("【答案】%s" % it["answer"])
                    ar.bold = True
                    ar.font.color.rgb = red
                if it["explanation"]:
                    pe = doc.add_paragraph()
                    er = pe.add_run("【解析】%s" % it["explanation"])
                    er.font.size = Pt(10)
                    er.font.color.rgb = grey
            elif not it["options"]:
                doc.add_paragraph("答：____________________________")

    foot = doc.add_paragraph()
    fr = foot.add_run("%s · 本卷由英语教学助手按班级错题生成，仅供教学使用，不得用于学生排名或评价。"
                      % ("教师版 · 含答案与解析" if with_answers else "学生卷"))
    fr.font.size = Pt(9)
    fr.font.color.rgb = grey

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- 单个学生批改报告（打印网页）

_VERDICT_CN = {"correct": ("对", "#248A3C"), "wrong": ("错", "#C93400"),
               "misspell": ("拼写错", "#C93400"), "blank": ("空白", "#8E8E93"),
               "uncertain": ("待判定", "#FF9500")}


def render_student_report_html(session: Dict[str, Any], result: Dict[str, Any],
                               name_local: str, feedback: Dict[str, str]) -> str:
    """单个学生批改结果 → A4 打印友好网页：成绩摘要 + 逐题明细 + AI 总结建议。

    只呈现该生本人的作答与错误描述，不与他人比较、不排名（反标签化）。
    session 为批改记录（title/class_name/kind/meta），result 为成绩单中该生的
    结果条目（作业/考试与听写两种明细形态），feedback 为 AI 总结与建议
    （由路由层调用 checking.student_feedback 生成，AI 失败时为空串）。
    """
    kind = session.get("kind") or "assignment"
    meta = session.get("meta") or {}
    code = result.get("student_code") or ""
    total = result.get("total") or result.get("judged_n") or 0
    score = result.get("score")
    head = ("<div class='head'><h1>%s · 批改报告</h1>"
            "<p>学生：%s（%s） · 班级：%s · %s</p></div>" % (
                _esc(session.get("title") or "批改结果"),
                _esc(name_local or code), _esc(code),
                _esc(session.get("class_name") or meta.get("class_name") or "未分班"),
                date.today().strftime("%Y 年 %m 月 %d 日")))

    stats = (
        "<div class='stats'>"
        "<div class='st'><div class='n'>%s</div><div>得分</div></div>"
        "<div class='st'><div class='n'>%d</div><div>正确数</div></div>"
        "<div class='st'><div class='n'>%d</div><div>总题数</div></div>"
        "<div class='st'><div class='n'>%d</div><div>待判定</div></div>"
        "</div>" % (
            _esc(score if score is not None else "—"),
            result.get("correct_n", 0), total,
            result.get("uncertain_n", 0)))

    rows = []
    if kind == "dictation":
        for w in (result.get("items") or []):
            v, color = _VERDICT_CN.get(w.get("verdict") or "", ("—", "#8E8E93"))
            rows.append(
                "<tr><td class='c'>%d</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='c' style='color:%s'><b>%s</b></td>"
                "<td class='muted'>%s</td></tr>" % (
                    w.get("no") or 0, _esc(w.get("en") or ""), _esc(w.get("zh") or ""),
                    _esc(w.get("answer") or "（空白）"), color, v,
                    _esc(w.get("note") or "")))
        thead = ("<tr><th>#</th><th>英文</th><th>中文</th><th>作答</th>"
                 "<th>判定</th><th>备注</th></tr>")
    else:
        for it in (result.get("items") or []):
            v, color = _VERDICT_CN.get(it.get("verdict") or "", ("—", "#8E8E93"))
            q = it.get("question") or ""
            opts = "<br>".join(_esc(o) for o in (it.get("options") or []))
            if opts:
                q += "<div class='opts'>%s</div>" % opts
            if it.get("passage"):
                q += "<div class='opts'>【原文】%s</div>" % _esc(it["passage"])
            got = it.get("got")
            rubric = ""
            if it.get("rubric"):
                rubric = ("<div class='rubric'>" + "　".join(
                    "%s：%s" % (_esc(d.get("name") or ""), _esc(d.get("comment") or ""))
                    for d in it["rubric"]) + "</div>")
            row = (
                "<tr><td class='c'>%d</td><td class='c'>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td class='c' style='color:%s'><b>%s</b></td>"
                "<td class='c'>%s</td></tr>" % (
                    it.get("no") or 0, _esc(it.get("qtype") or ""),
                    q, _esc(it.get("answer") or "（空白）"),
                    _esc(it.get("correct") or "—"), color, v,
                    _esc(("%s / %s" % (got, it.get("score"))) if got is not None else "—")))
            if it.get("note") or rubric:
                row += ("<tr><td></td><td colspan='6' class='muted'>%s%s</td></tr>" % (
                    _esc(it.get("note") or ""), rubric))
            rows.append(row)
        thead = ("<tr><th>#</th><th>题型</th><th>题目（含选项/原文）</th><th>学生作答</th>"
                 "<th>标准答案</th><th>判定</th><th>得分</th></tr>")

    fb = []
    if feedback.get("summary"):
        fb.append("<p><b>总结：</b>%s</p>" % _esc(feedback["summary"]))
    if feedback.get("advice"):
        fb.append("<p><b>学习建议：</b>%s</p>" % _esc(feedback["advice"]))
    feedback_html = ("<div class='fb'>" + "".join(fb) + "</div>") if fb else ""

    return """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>批改报告（%(code)s）</title>
<style>
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Songti SC", "SimSun", "Noto Serif SC", serif;
  color: #111; max-width: 182mm; margin: 0 auto; padding: 10mm 4mm; line-height: 1.8; }
.noprint { position: fixed; right: 14px; top: 14px; }
@media print { .noprint { display: none; } }
.head { text-align: center; border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 14px; }
.head h1 { font-size: 20px; margin: 0 0 4px; }
.head p { font-size: 12.5px; color: #333; margin: 0; }
.stats { display: flex; gap: 10px; margin: 12px 0; }
.st { flex: 1; text-align: center; border: 1px solid #ccc; border-radius: 8px; padding: 8px 4px; }
.st .n { font-size: 18px; font-weight: 800; color: #2b6cb0; }
.st div:last-child { font-size: 12px; color: #666; }
table { width: 100%%; border-collapse: collapse; font-size: 12.5px; margin-top: 8px; }
th, td { border: 1px solid #bbb; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eee; }
td.c { text-align: center; white-space: nowrap; }
.muted { color: #777; font-size: 12px; }
.opts { color: #555; font-size: 12px; margin-top: 3px; }
.rubric { color: #555; font-size: 12px; }
.fb { border: 1px solid #cfe3ff; background: #f3f8ff; border-radius: 8px;
  padding: 10px 14px; margin-top: 14px; font-size: 13px; }
.foot { margin-top: 20px; border-top: 1px solid #999; padding-top: 6px;
  font-size: 11px; color: #777; text-align: center; }
</style></head><body>
<button class="noprint" onclick="window.print()">🖨 打印 / 另存 PDF</button>
%(head)s
%(stats)s
<table>%(thead)s%(rows)s</table>
%(feedback)s
<div class="foot">本报告只描述本次作答的错误本身，不评价学生个人，不用于排名或综合素质评价。</div>
</body></html>""" % {"code": _esc(code), "head": head, "stats": stats,
                     "thead": thead, "rows": "".join(rows), "feedback": feedback_html}


# ---------------------------------------------------------------- 学生错题档案报告

_GROUP_NAMES = {"A": "词汇知识", "B": "语法结构", "C": "语篇理解",
                "D": "书面表达", "E": "答题行为"}


def _clip(s: str, n: int) -> str:
    """题干/作答截断（报告表格用，避免长文撑爆版面）。"""
    t = " ".join(str(s or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def render_student_trend_report_html(p: Dict[str, Any]) -> str:
    """学生错题档案 → A4 打印友好网页：统计 + 错因分布 + 月度趋势 + 最近错题。

    p 为 db.student_profile(code) 的返回（含 student/categories/trend/recent）。
    只呈现原始错题与计数，不生成评价性结论、不与他人比较（反标签化）。
    """
    from ..data import ontology
    stu = p.get("student") or {}
    code = stu.get("student_code") or ""
    name = stu.get("name_local") or ""
    cats = p.get("categories") or []
    trend = p.get("trend") or []
    recent = p.get("recent") or []

    head = ("<div class='head'><h1>学生错题档案报告</h1>"
            "<p>学生：%s（%s） · 班级：%s · %s</p></div>" % (
                _esc(name or code), _esc(code),
                _esc(stu.get("class_name") or "未分班"),
                date.today().strftime("%Y 年 %m 月 %d 日")))

    stats = (
        "<div class='stats'>"
        "<div class='st'><div class='n'>%d</div><div>确认错题</div></div>"
        "<div class='st'><div class='n'>%d</div><div>待确认</div></div>"
        "<div class='st'><div class='n'>%d</div><div>涉及错因类别</div></div>"
        "<div class='st'><div class='n'>%d</div><div>趋势月数</div></div>"
        "</div>" % (p.get("total_confirmed") or 0, p.get("pending") or 0,
                    len(cats), len(trend)))

    cat_rows = "".join(
        "<tr><td class='c'>%s</td><td>%s</td><td class='c'>%s</td>"
        "<td class='c'><b>%d</b></td></tr>" % (
            _esc(c["category_id"]),
            _esc(ontology.category_name(c["category_id"])),
            _esc(_GROUP_NAMES.get((c["category_id"] or "?")[:1], "—")),
            c["count"])
        for c in cats) or "<tr><td colspan='4' class='muted'>暂无已确认错因</td></tr>"

    max_t = max([t["total"] for t in trend] + [1])
    trend_rows = "".join(
        "<tr><td class='c'>%s</td>"
        "<td><div class='bar'><span style='width:%d%%'></span></div></td>"
        "<td class='c'><b>%d</b></td><td class='c'>%d</td><td class='c'>%d</td>"
        "<td class='c'>%d</td><td class='c'>%d</td><td class='c'>%d</td></tr>" % (
            _esc(t["ym"]), round(t["total"] / max_t * 100), t["total"],
            t.get("gA") or 0, t.get("gB") or 0, t.get("gC") or 0,
            t.get("gD") or 0, t.get("gE") or 0)
        for t in trend) or "<tr><td colspan='8' class='muted'>暂无错题记录</td></tr>"

    delta = ""
    if len(trend) >= 2:
        d = trend[-1]["total"] - trend[-2]["total"]
        word = "多 %d 条" % d if d > 0 else "少 %d 条" % -d if d < 0 else "持平"
        delta = ("<p class='muted' style='margin:4px 0 0'>最近一月（%s）共 %d 条，"
                 "较前一月%s。</p>" % (_esc(trend[-1]["ym"]), trend[-1]["total"], word))

    recent_rows = "".join(
        "<tr><td class='c'>%s</td><td class='c'>%s</td><td class='c'>%s</td>"
        "<td>%s</td><td>%s</td><td class='muted'>%s</td></tr>" % (
            _esc((e.get("created_at") or "")[:10]),
            _esc(e.get("teacher_override") or e.get("category_id") or "—"),
            _esc(e.get("qtype") or "—"),
            _esc(_clip(e.get("question"), 70)),
            _esc(_clip(e.get("answer"), 30) or "（空白）"),
            _esc(" · ".join(x for x in (e.get("exam_type"), e.get("batch_no")) if x)))
        for e in recent) or "<tr><td colspan='6' class='muted'>暂无错题记录</td></tr>"

    return """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>学生错题档案（%(code)s）</title>
<style>
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Songti SC", "SimSun", "Noto Serif SC", serif;
  color: #111; max-width: 182mm; margin: 0 auto; padding: 10mm 4mm; line-height: 1.8; }
.noprint { position: fixed; right: 14px; top: 14px; }
@media print { .noprint { display: none; } }
.head { text-align: center; border-bottom: 2px solid #111; padding-bottom: 8px; margin-bottom: 14px; }
.head h1 { font-size: 20px; margin: 0 0 4px; }
.head p { font-size: 12.5px; color: #333; margin: 0; }
.stats { display: flex; gap: 10px; margin: 12px 0; }
.st { flex: 1; text-align: center; border: 1px solid #ccc; border-radius: 8px; padding: 8px 4px; }
.st .n { font-size: 18px; font-weight: 800; color: #2b6cb0; }
.st div:last-child { font-size: 12px; color: #666; }
h2 { font-size: 15px; margin: 18px 0 4px; }
table { width: 100%%; border-collapse: collapse; font-size: 12.5px; margin-top: 8px; }
th, td { border: 1px solid #bbb; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eee; }
td.c { text-align: center; white-space: nowrap; }
.muted { color: #777; font-size: 12px; }
.bar { background: #eee; border-radius: 99px; height: 10px; overflow: hidden; min-width: 80px; }
.bar span { display: block; height: 100%%; background: #7C3AED; }
.foot { margin-top: 20px; border-top: 1px solid #999; padding-top: 6px;
  font-size: 11px; color: #777; text-align: center; }
</style></head><body>
<button class="noprint" onclick="window.print()">🖨 打印 / 另存 PDF</button>
%(head)s
%(stats)s
<h2>① 错因分布（已确认的，按次数降序）</h2>
<table><tr><th>编号</th><th>错因名称</th><th>所属组</th><th>次数</th></tr>%(cat_rows)s</table>
<h2>② 月度趋势（错题数与五大组分布，按录入时间）</h2>
<table><tr><th>月份</th><th>错题数（条形）</th><th>合计</th><th>词汇A</th><th>语法B</th><th>语篇C</th><th>表达D</th><th>行为E</th></tr>%(trend_rows)s</table>
%(delta)s
<h2>③ 最近错题（前 %(recent_n)d 条）</h2>
<table><tr><th>日期</th><th>错因</th><th>题型</th><th>题干</th><th>作答</th><th>来源</th></tr>%(recent_rows)s</table>
<div class="foot">本报告仅供任课教师辅导参考：只呈现原始错题与计数，不构成学生评价，不得用于排名或综合素质评定；代号与姓名的对应关系只保存在教师本机。</div>
</body></html>""" % {"code": _esc(code), "head": head, "stats": stats,
                     "cat_rows": cat_rows, "trend_rows": trend_rows,
                     "delta": delta, "recent_rows": recent_rows,
                     "recent_n": len(recent)}
