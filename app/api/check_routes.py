"""批改路由：听写批改 / 作业批改（POST）+ 拍照读纸 + 历史记录与导出。

"批改即收集"：判错的题由批改引擎自动进入错因分析链路（待确认），
本层只负责请求校验、调用引擎、留存快照。
统一信息（班级/单元/周次/测试类型/标题）在批改时录入、事后可手动修改。
反标签化：学生代号只在本机数据库内关联，不进入任何模型请求（引擎层保证）；
导出报告只描述错误，不评价学生个人。
"""
import csv
import io
import logging
import re
import urllib.parse
import zipfile
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from ..data import db
from ..services import checking, export_docs, photo_check, uploads
from . import schemas

router = APIRouter(prefix="/api/check")

logger = logging.getLogger("eta.api.check")


# ---------------------------------------------------------------- 听写批改

@router.post("/dictation")
def dictation_check(req: schemas.DictationCheckRequest):
    """听写批改：词表 + 作答 → 规则判卷 → 错词自动分析错因 → 成绩单。"""
    words = [w.model_dump() if hasattr(w, "model_dump") else dict(w) for w in req.words]
    answers = [a.model_dump() if hasattr(a, "model_dump") else dict(a)
               for a in req.answers]
    if not words:
        raise HTTPException(400, "词表为空：请先填写本次听写的词表（每行一个「英文,中文」）")
    if not answers:
        raise HTTPException(400, "没有学生作答：请按「学号 | 词1,词2…」格式粘贴作答")
    try:
        report = checking.dictation_check(
            words, answers, req.unit, req.week, req.class_name or "",
            (req.title or "").strip(), req.exam_type or "",
            batch_no=(req.batch_no or "").strip())
    except Exception as exc:  # noqa: BLE001 —— 批改失败给直白提示，细节进日志
        logger.exception("听写批改失败")
        raise HTTPException(500, "批改遇到问题，请稍后重试（%s）" % str(exc)[:80])
    error_ids = report.pop("inserted_error_ids", [])
    session_id = db.insert_check_session(
        "dictation", report["title"], req.class_name or "", req.unit, req.week,
        req.exam_type, report, batch_no=(req.batch_no or "").strip() or None)
    db.set_errors_session(error_ids, session_id)
    report["session_id"] = session_id
    return report


# ---------------------------------------------------------------- 作业/考试批改

@router.post("/assignment")
def assignment_check(req: schemas.AssignmentCheckRequest):
    """作业/考试批改：答案 + 作答 → 客观题规则判卷 / 主观题 AI 判定 → 错题自动入库 → 成绩单。

    文件夹上传场景：folder/batch_no（三段式命名）随报告留存，answers 的
    name/files 登记到 paper_files，item 的 file 写入错题 source——
    班级+批改类型+唯一标识定位一次提交，学生姓名定位一个人，学生/文件名定位一张照片。
    """
    key = [k.model_dump() if hasattr(k, "model_dump") else dict(k) for k in req.key]
    answers = [a.model_dump() if hasattr(a, "model_dump") else dict(a)
               for a in req.answers]
    if not key:
        raise HTTPException(400, "还没有题目答案：请先按「题号 | 题型 | 题干 | 答案 | 分值」逐题填写")
    if not answers:
        raise HTTPException(400, "没有学生作答：请按「学号 | 1.A,2.difficult…」格式粘贴作答")
    folder = (req.folder or "").strip()
    batch_no = (req.batch_no or "").strip()
    if not batch_no and folder:
        try:
            batch_no = uploads.parse_batch_folder(folder, req.exam_type or "")["batch_no"]
        except ValueError:
            batch_no = ""
    try:
        report = checking.assignment_check(
            key, answers, req.unit, req.week, req.class_name or "",
            (req.title or "").strip(), req.exam_type or "",
            folder=folder, batch_no=batch_no)
    except Exception as exc:  # noqa: BLE001
        logger.exception("作业批改失败")
        raise HTTPException(500, "批改遇到问题，请稍后重试（%s）" % str(exc)[:80])
    error_ids = report.pop("inserted_error_ids", [])
    session_id = db.insert_check_session(
        "assignment", report["title"], req.class_name or "", req.unit, req.week,
        req.exam_type, report, batch_no=batch_no or None)
    db.set_errors_session(error_ids, session_id)
    # 上传文件登记：answers 的 files 携带该生照片清单（相对批次根目录）
    file_rows = [{
        "session_id": session_id,
        "class_name": req.class_name or "",
        "batch_no": batch_no,
        "student_code": (a.get("student_code") or "").strip(),
        "student_name": (a.get("name") or "").strip(),
        "file_name": f,
    } for a in answers for f in (a.get("files") or []) if f]
    if file_rows:
        db.insert_paper_files(file_rows)
    report["session_id"] = session_id
    return report


# ---------------------------------------------------------------- 上传组织（三段式批次文件夹）

@router.post("/organize-upload")
def organize_upload(req: schemas.OrganizeUploadRequest):
    """上传预检：解析批次文件夹路径 → 学生分组 + 统一信息预填建议（不读照片，瞬时）。

    命名规范：班级-批改类型-唯一标识 / 学生姓名 / 照片；也支持两段式
    「班级-唯一标识」（批改类型取 req.exam_type，即当前批改页）。
    结构小问题以 warnings 返回，命名不合规返 400（含改名建议）。
    """
    if not req.paths:
        raise HTTPException(400, "没有选择照片：请选择按「班级-批改类型-唯一标识/学生姓名」整理的文件夹")
    try:
        return uploads.organize(req.paths, req.exam_type or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- 拍照批改解析（批改组主入口）

@router.post("/photo-parse")
def photo_parse(req: schemas.PhotoParseRequest):
    """上传批次文件夹照片 → 按学生分组 AI 区分题目内容与学生作答 → 教师核对后走既有批改引擎。

    只解析不批改：返回每个学生（文件夹）的姓名匹配与题目/作答/参考答案，
    以及每题的来源照片（file），前端核对后仍提交 /assignment 端点
    （复用规则判卷 + AI 判定，错题据此关联到具体上传文件）。
    exam_type 为当前批改页的批改类型（两段式批次根命名时使用）；
    class_name 为批改页右上角所选班级（名册匹配限定该班）。
    """
    items = [{"path": it.path, "image_base64": it.image_base64}
             for it in req.items if it.image_base64]
    if not items:
        raise HTTPException(400, "没有照片：请选择按「班级-批改类型-唯一标识/学生姓名」整理的文件夹")
    try:
        return photo_check.parse_papers(items, req.exam_type or "", req.class_name or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("拍照批改解析失败")
        raise HTTPException(500, "解析照片遇到问题，请稍后重试（%s）" % str(exc)[:80])


@router.post("/md-reparse")
def md_reparse(req: schemas.MdReparseRequest):
    """智能体提取原文修改后重新结构化：老师编辑 Markdown 原文（修正识别错误/补漏），
    按批改类型重新解析出该生的题目/作答/参考答案（同 photo-parse 结构化规则）。"""
    md = (req.markdown or "").strip()
    if not md:
        raise HTTPException(400, "原文为空：请先补全后再应用修改")
    try:
        out = photo_check._md_questions(md, req.exam_type or "")
        return {"name": out.get("name", ""), "questions": out.get("questions", [])}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("原文修改重新结构化失败")
        raise HTTPException(500, "重新解析遇到问题，请稍后重试（%s）" % str(exc)[:80])


# ---------------------------------------------------------------- 历史记录

@router.get("/sessions")
def list_sessions(kind: Optional[str] = None, exam_type: Optional[str] = None,
                  class_name: Optional[str] = None,
                  limit: int = Query(20, le=100)):
    """最近批改记录（概要数字，不含全量明细）；exam_type 用于作业/考试两页隔离，
    class_name 跟随批改页右上角班级过滤。"""
    return {"sessions": db.list_check_sessions(
        limit=limit, kind=kind, exam_type=exam_type or None,
        class_name=class_name or None)}


@router.get("/sessions/{session_id}")
def get_session(session_id: int):
    """某次批改的完整报告（成绩单与逐题明细）。"""
    s = db.get_check_session(session_id)
    if not s:
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    return s


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, purge_errors: bool = False):
    """删除一条批改历史：默认保留已收集错题（仅解除关联），
    purge_errors=1 时连同本次收集的错题一并删除。"""
    removed = db.delete_check_session(session_id, purge_errors=purge_errors)
    if removed is None:
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    return {"ok": True, "removed_errors": removed, "purged": bool(purge_errors)}


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, req: schemas.SessionUpdateRequest):
    """手动修改统一信息：班级/单元/周次/批改类型/唯一标识/标题（未传字段不改）。"""
    ok = db.update_check_session(
        session_id, title=req.title, class_name=req.class_name,
        unit=req.unit, week=req.week, exam_type=req.exam_type,
        batch_no=req.batch_no)
    if not ok:
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    return get_session(session_id)


@router.get("/sessions/{session_id}/files")
def session_files(session_id: int):
    """某次批改登记的上传文件清单（定位链：批次 → 学生 → 照片）。"""
    if not db.get_check_session(session_id):
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    return {"files": db.list_paper_files(session_id)}


# ---------------------------------------------------------------- 按学生导出（含 AI 总结与建议）

def _session_roster(s: Dict) -> Dict[str, str]:
    """本次批改班级的名册：学号 → 姓名（单生导出与批量导出共用）。"""
    return {st["student_code"]: (st.get("name_local") or "")
            for st in db.list_students(s.get("class_name") or "")}


def _student_report_html(s: Dict, result: Dict, roster: Dict[str, str]) -> str:
    """单生结果 → 打印友好网页（AI 总结失败留空不阻断）。"""
    code = (result.get("student_code") or "").strip()
    fb = checking.student_feedback(result.get("items") or [],
                                   s.get("kind") or "assignment")
    return export_docs.render_student_report_html(
        s, result, roster.get(code, ""), fb)


@router.get("/sessions/{session_id}/export")
def export_session(session_id: int, student_code: Optional[str] = None):
    """导出批改结果：student_code 给定时导出该生个人报告（可打印网页：成绩摘要、
    逐题明细、AI 总结与建议）；否则导出整班汇总 CSV（成绩与错题计数 + AI 总结与建议）。"""
    s = db.get_check_session(session_id)
    if not s:
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    report = s["report"]

    # ---- 单个学生：打印友好网页报告（只描述该生错误，不排名、不比较） ----
    if student_code:
        code = (student_code or "").strip()
        result = next((r for r in (report.get("results") or [])
                       if (r.get("student_code") or "").strip() == code), None)
        if not result:
            raise HTTPException(404, "该生在本次批改中没有结果：%s" % code)
        html = _student_report_html(s, result, _session_roster(s))
        logger.info("导出单生批改报告：%s（session=%d）", code, session_id)
        return HTMLResponse(html)

    # ---- 整班汇总 CSV（教师本机汇总，不排名） ----
    kind = s["kind"]
    roster = _session_roster(s)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["学号", "姓名", "得分", "正确数", "总题数", "待判定", "错题数",
                     "AI 总结", "学习建议"])
    for r in report.get("results") or []:
        code = r.get("student_code") or ""
        items = r.get("items") or []
        wrong_n = sum(1 for it in items if it.get("verdict") in ("wrong", "misspell", "blank"))
        fb = checking.student_feedback(items, kind)
        writer.writerow([
            code, roster.get(code, ""), r.get("score"),
            r.get("correct_n", 0), r.get("total", r.get("judged_n", len(items))),
            r.get("uncertain_n", 0), wrong_n,
            fb["summary"], fb["advice"],
        ])
    filename = "批改结果_%s.csv" % (s.get("title") or "export")
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%s"
                 % urllib.parse.quote(filename)})


@router.get("/sessions/{session_id}/export-all")
def export_session_all(session_id: int):
    """导出全部学生的个人报告：每生一个打印友好网页，打包 zip 一次下载。

    与单生导出同源同口径（逐生生成 AI 总结与建议，失败留空不阻断）；
    文件名「学号_姓名.html」，解决逐个点单生报告导出的重复操作。
    """
    s = db.get_check_session(session_id)
    if not s:
        raise HTTPException(404, "批改记录 %d 不存在" % session_id)
    results = (s["report"].get("results") or [])
    if not results:
        raise HTTPException(400, "本次批改没有学生结果，无法导出")
    roster = _session_roster(s)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for result in results:
            code = (result.get("student_code") or "").strip()
            name = roster.get(code, "")
            html = _student_report_html(s, result, roster)
            safe = re.sub(r'[\\/:*?"<>|]+', "_",
                          "%s_%s" % (code or "学生", name)).strip("_")
            zf.writestr("批改报告_%s.html" % safe, html)
    filename = "批改单生报告_%s.zip" % (s.get("title") or "export")
    logger.info("导出全部单生批改报告：%d 名学生（session=%d）", len(results), session_id)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%s"
                             % urllib.parse.quote(filename)})
