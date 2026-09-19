"""导出路由：练习/试卷文档（HTML 打印页 + Word）、学生错题档案报告与学生综合信息 CSV。

合规边界（与业务路由一致的反标签化约束）：
  - 题目类导出只含题目本身，不含任何学生数据；学生卷（无答案版）仅限
    教师审校通过（approved）的内容，教师版（含答案解析）不受限——
    可作为审校辅助（对应"加强内容审查把关"）；
  - 学生错题档案报告与综合信息 CSV 是教师本机汇总：只呈现原始计数、
    错因分布与月度趋势，不做任何排名，不生成评价性结论。
"""
import csv
import io
import logging
import re
import zipfile
from datetime import date
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

from ..data import db, ontology
from ..services import export_docs

router = APIRouter(prefix="/api")

logger = logging.getLogger("eta.export")


def _docx_response(buf: io.BytesIO, filename: str) -> StreamingResponse:
    """Word 下载（流式）：RFC 5987 编码中文文件名（Safari/Chrome 均兼容）。"""
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument."
                   "wordprocessingml.document",
        headers={"Content-Disposition":
                 "attachment; filename*=UTF-8''%s.docx" % quote(filename)},
    )


def _parse_ids(raw: str) -> List[int]:
    """逗号分隔的 id 串 → 去重保序的 int 列表；非法输入直接 400。"""
    try:
        ids = [int(x) for x in (raw or "").split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids 格式不对：应为逗号分隔的练习编号，如 1,2,3")
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- 练习导出

@router.get("/practices/export")
def export_practices(ids: str = Query(..., description="逗号分隔的练习 id"),
                     fmt: Literal["html", "docx"] = "html",
                     answers: int = Query(0, ge=0, le=1)):
    """导出一套或多套练习：html=可打印网页（新标签页打开）；docx=Word 下载。

    answers=1 教师版（含答案与解析）；answers=0 学生卷（仅限已审校练习，
    未经教师把关的 AI 生成内容不下发——审校环节不可绕过）。
    """
    practices = []
    for pid in _parse_ids(ids):
        p = db.get_practice(pid)
        if not p:
            raise HTTPException(404, "练习 %d 不存在" % pid)
        if not answers and p.get("status") != "approved":
            raise HTTPException(400, "练习 %d 还没审校——学生卷只能导出已审校通过的练习；"
                                     "导出教师版（含答案）可先打印核对" % pid)
        practices.append(p)
    if not practices:
        raise HTTPException(400, "请至少选择一套练习")

    meta = export_docs.practices_doc(practices)
    ver = "教师版" if answers else "学生卷"
    fname = "配套练习_%s_%s_%s" % (meta["class_name"] or "全班级",
                                    date.today().strftime("%Y%m%d"), ver)
    if fmt == "docx":
        return _docx_response(export_docs.render_docx(meta, bool(answers)), fname)
    return HTMLResponse(export_docs.render_html(meta, bool(answers)))


# ---------------------------------------------------------------- 试卷导出

@router.get("/papers/{paper_id}/export")
def export_paper(paper_id: int,
                 fmt: Literal["html", "docx"] = "html",
                 answers: int = Query(0, ge=0, le=1)):
    """导出 AI 组卷的试卷：html=可打印网页；docx=Word 下载（教师版/学生卷）。"""
    p = db.get_paper(paper_id)
    if not p:
        raise HTTPException(404, "试卷 %d 不存在" % paper_id)
    if not answers and p.get("status") != "approved":
        raise HTTPException(400, "试卷还没审校——学生卷只能导出已审校通过的试卷；"
                                 "导出教师版（含答案）可先打印核对")

    meta = export_docs.paper_doc(p)
    ver = "教师版" if answers else "学生卷"
    fname = "试卷_%s_%s_%s" % (p.get("title") or "综合测试",
                                date.today().strftime("%Y%m%d"), ver)
    if fmt == "docx":
        return _docx_response(export_docs.render_docx(meta, bool(answers)), fname)
    return HTMLResponse(export_docs.render_html(meta, bool(answers)))


# ---------------------------------------------------------------- 讲评方案（决策单）导出

@router.get("/decision-sheet/export")
def export_decision_sheet(class_name: Optional[str] = None,
                          exam_type: Optional[str] = None,
                          batch_nos: Optional[str] = None,
                          date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          fmt: Literal["html", "docx"] = "html"):
    """讲评方案导出：html=打印友好网页（A4，新标签页打开即可打印）；docx=Word 下载。

    与页面同源：同一范围（班级 + 任务类型 + 批次/阶段）实时重算，不含学生姓名。
    """
    from ..services import decision

    batches = [b.strip() for b in (batch_nos or "").split(",") if b.strip()]
    sheet = decision.build_decision_sheet(
        class_name=class_name, exam_type=exam_type,
        batch_nos=batches or None, date_from=date_from or None,
        date_to=date_to or None, save=False)
    fname = "讲评方案_%s_%s" % (class_name or "全部班级",
                                date.today().strftime("%Y%m%d"))
    if fmt == "docx":
        return _docx_response(export_docs.render_sheet_docx(sheet), fname)
    return HTMLResponse(export_docs.render_sheet_html(sheet))


# ---------------------------------------------------------------- 学生错题档案报告

@router.get("/students/reports.zip")
def export_students_reports_zip(class_name: Optional[str] = None):
    """全部学生的错题档案报告：每生一个打印友好网页（统计 + 错因分布 +
    月度趋势 + 最近错题），zipfile 打包一次下载。

    与单生导出同源同口径；文件名「错题档案_代号_姓名.html」，解决
    逐个点单生报告导出的重复操作（与批改页全部单生报告同思路）。
    """
    students = db.list_students(class_name)
    if not students:
        raise HTTPException(400, "没有可导出的学生（请先在「学生管理」页导入名册）")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in students:
            p = db.student_profile(s["student_code"])
            if not p:
                continue
            safe = re.sub(r'[\\/:*?"<>|]+', "_",
                          "%s_%s" % (s["student_code"], s.get("name_local") or "")).strip("_")
            zf.writestr("错题档案_%s.html" % safe,
                        export_docs.render_student_trend_report_html(p))
    filename = "学生错题档案_%s.zip" % (class_name or "全部班级")
    logger.info("导出学生错题档案 zip：%d 名学生（class=%s）",
                len(students), class_name or "")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             "attachment; filename*=UTF-8''%s" % quote(filename)})


@router.get("/students/{code}/report")
def export_student_report(code: str):
    """单个学生错题档案报告（可打印网页）：统计 + 错因分布 + 月度趋势 + 最近错题。

    仅供任课教师本机辅导参考；只呈现原始记录与计数，不评价、不排名。
    """
    p = db.student_profile(code)
    if not p:
        raise HTTPException(404, "学生不存在：%s" % code)
    logger.info("导出学生错题档案报告：%s", code)
    return HTMLResponse(export_docs.render_student_trend_report_html(p))


# ---------------------------------------------------------------- 学生综合信息 CSV

@router.get("/students/export.csv")
def export_students_csv(class_name: Optional[str] = None):
    """学生综合信息 CSV（utf-8-sig 带 BOM，Excel 直开）——教师本机汇总。

    列：代号 / 姓名 / 班级 / 已确认错题数 / 待确认数 / 主要错因（TOP3，
    名称×次数）/ 批改次数 / 平均分。只聚合原始数据，不排名、不评价。
    """
    students = db.list_students(class_name)
    if not students:
        raise HTTPException(400, "没有可导出的学生（请先在「学生名单」页导入名册）")

    scores = db.student_check_scores(class_name)
    buf = io.StringIO()
    buf.write("\ufeff")   # BOM：Excel 双击直开不乱码
    w = csv.writer(buf)
    w.writerow(["student_code", "姓名", "班级", "已确认错题数", "待确认数",
                "主要错因(TOP3)", "批改次数", "平均分"])
    for s in students:
        code = s["student_code"]
        p = db.student_profile(code) or {}
        cats = (p.get("categories") or [])[:3]
        top = "；".join("%s×%d" % (ontology.category_name(c["category_id"]), c["count"])
                       for c in cats) or "—"
        sc = scores.get(code) or []
        w.writerow([
            code, s.get("name_local") or "", s.get("class_name") or "",
            p.get("total_confirmed") or 0, p.get("pending") or 0, top,
            len(sc), round(sum(sc) / len(sc), 1) if sc else "",
        ])

    fname = "学生综合信息_%s_%s.csv" % (class_name or "全部班级",
                                        date.today().strftime("%Y%m%d"))
    logger.info("导出学生综合信息：%d 名学生（class=%s）", len(students), class_name or "")
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename*=UTF-8''%s" % quote(fname)},
    )
