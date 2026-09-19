"""全部 HTTP 路由（APIRouter 按资源分组，main.py 统一挂载）。

反标签化路由约束：
  本文件不存在任何"按学生输出评价/排名"的端点——从路由上杜绝
  "利用数据实施歧视"的可能（对应《教师生成式AI应用指引》"处理学业数据"）。
  唯一例外：GET /students/{code}/profile 为教师个体帮扶视图（仅本机查看，
  返回原始错题与计数含姓名，不产生任何评价性结论与排名）。
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from ..core import config
from ..data import db, ontology
from ..providers import ocr
from ..services import classifier, decision, decision_ai, paper, practice, report
from . import schemas

router = APIRouter(prefix="/api")

logger = logging.getLogger("eta.api")


def _ocr_fail_msg(exc: Exception) -> str:
    """OCR 失败给老师的直白提示；技术细节记在服务端日志，不弹给用户。"""
    logger.warning("OCR 失败：%s", exc)
    if "未配置" in str(exc):
        return "照片识别功能还没配置好，请联系技术人员检查设置后再试。"
    return "照片识别失败，请稍后重试；也可以先手动输入题目，不影响录入。"


# ---------------------------------------------------------------- 健康与本体库

@router.get("/health")
def health():
    return {
        "status": "ok",
        "app": "eta-assistant",
        "version": config.APP_VERSION,
        "llm": {
            "model": config.LLM_MODEL,
            "available": config.llm_available(),
            "endpoint_kind": "deepseek" if config.is_deepseek_model() else "openai-compatible",
        },
        "ocr": {
            "provider": ocr.provider_name(),
            "available": config.ocr_available(),
        },
        "photo_parse": {
            "provider": "agent" if config.use_agent_parse() else "ocr",
            "agent_available": config.agent_chat_available(),
        },
        "ontology_categories": len(ontology.all_categories()),
    }


@router.get("/ontology")
def get_ontology():
    """本体库（审校页下拉用）。"""
    data = ontology.load_ontology()
    return {
        "version": data["version"],
        "groups": data["groups"],
        "categories": [
            {"id": c["id"], "group": c["group"], "name": c["name"],
             "signals": c["signals"], "teaching_point": c["teaching_point"]}
            for c in data["categories"]
        ],
    }


@router.get("/classes")
def get_classes():
    """班级列表（前端全局选择器）。"""
    return {"classes": db.list_classes()}


@router.post("/classes")
def create_class(req: schemas.ClassCreateRequest):
    """新建班级：可先建班，再录学生/错题（动态初始化，无需预先全部建完）。"""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "班级名不能为空")
    if name in db.list_classes():
        raise HTTPException(400, "班级 %s 已存在（可在学生名单页点 ✎ 改名）" % name)
    db.add_class(name)
    return {"ok": True, "name": name}


@router.put("/classes/{name}")
def rename_class(name: str, req: schemas.ClassRenameRequest):
    """班级改名：级联更新该班全部学生与错题的归属，不断链。"""
    new_name = (req.new_name or "").strip()
    if not new_name:
        raise HTTPException(400, "新班级名不能为空")
    if new_name == name:
        return {"ok": True, "name": name}
    if new_name in db.list_classes():
        raise HTTPException(400, "班级 %s 已存在，不能改名为重复的名字" % new_name)
    db.rename_class(name, new_name)
    return {"ok": True, "name": new_name}


@router.delete("/classes/{name}")
def delete_class(name: str):
    """删除班级：连同该班全部数据（学生 / 错题 / 补偿练习 / 组卷 / 批改记录 / 照片登记）一并删除。"""
    stu, err = db.delete_class(name)
    return {"ok": True, "removed_students": stu, "removed_errors": err}


# ---------------------------------------------------------------- 录入与归因

@router.post("/errors/scan")
def scan(req: schemas.ScanRequest):
    """文本粘贴（主）/ 图片 OCR（兜底）→ 归因 → 待确认入库。"""
    question = (req.question or "").strip()
    qtype = req.qtype or ""
    ocr_note = None
    if req.image_base64 and not question:
        try:
            result = ocr.ocr_image(ocr.normalize_image(req.image_base64))
        except Exception as exc:  # noqa: BLE001 —— OCR 失败给老师直白提示，细节见日志
            raise HTTPException(400, _ocr_fail_msg(exc))
        question = result["question"]
        qtype = qtype or result.get("qtype", "")
        ocr_note = result.get("note")
        # OCR 拆出的阅读/完形原文（【原文】标记）：仅当请求未自带时采用
        if not (req.passage or "").strip() and result.get("passage"):
            req.passage = result["passage"]

    if not question:
        raise HTTPException(400, "缺少题干：请粘贴文本或上传图片")

    # 反标签化：传给大模型的内容只有题目本身，student_code 不进入任何模型 payload
    ai = classifier.classify(question, req.answer or "", req.correct or "",
                             qtype, req.options, passage=(req.passage or ""))
    behavior = None
    if req.student_code:
        behavior = classifier.review_behavior(req.student_code, ai["category_id"],
                                              question, req.class_name or "")

    error_id = db.insert_error({
        "student_code": req.student_code or "",
        "class_name": req.class_name or "",
        "question": question, "answer": req.answer, "correct": req.correct,
        "qtype": qtype, "passage": (req.passage or "") or None,
        "options": req.options, "unit": req.unit, "week": req.week,
        "exam_type": req.exam_type, "batch_no": req.batch_no,
        "category_id": ai["category_id"], "evidence": ai["evidence"],
        "teaching_point": ai["teaching_point"], "confidence": ai["confidence"],
        "needs_review": ai["needs_review"], "behavior": behavior,
    })
    return {"error_id": error_id, "ocr_note": ocr_note, "ai": ai, "behavior": behavior,
            "passage": (req.passage or ""), "qtype": qtype,
            "needs_confirm": True}


# 批量行首段"学号串"判定：无空格的字母/数字/逗号/连字符（如 9101,9103）
_BATCH_CODE_RE = re.compile(r"^[0-9A-Za-z，,\-]+$")


def _parse_batch_line(line: str, roster: dict) -> tuple:
    """解析批量行 → (codes, question, answer, correct, err)。

    规则：
      - 分隔符 | 或 Tab（支持从 Excel 直接粘贴）；
      - 首段为空且段数≥3 → 无学号占位，右移一段；
      - 首段形如学号串（无空格字母数字逗号）→ 全部在本班名册才作学号段，
        否则报错提示（避免写错学号被静默当成题干）；
      - 其余情况首段即题干（至少「题干 | 学生作答」两段）。
    """
    sep = "|" if "|" in line else "\t"
    parts = [p.strip() for p in line.split(sep)]
    codes = []
    if len(parts) >= 3 and not parts[0]:
        parts = parts[1:]
    if len(parts) >= 3 and parts[0] and " " not in parts[0] and _BATCH_CODE_RE.match(parts[0]):
        tokens = [t for t in parts[0].replace("，", ",").split(",") if t]
        missing = [t for t in tokens if t not in roster]
        if missing:
            return [], "", "", "", ("首段「%s」不是有效学号（%s 不在本班名册）——请先到「学生」页导入名册，"
                                    "或不关联学生时去掉首段学号" % (parts[0], "、".join(missing)))
        codes, parts = tokens, parts[1:]
    if len(parts) < 2:
        return [], "", "", "", "格式不对：每行至少「题干 | 学生作答」两段（分隔符 | 或 Tab）"
    question, answer = parts[0], parts[1]
    correct = parts[2] if len(parts) > 2 else ""
    if not question:
        return [], "", "", "", "题干为空"
    if not answer:
        return [], "", "", "", "学生作答为空"
    return codes, question, answer, correct, None


@router.post("/errors/scan-batch")
def scan_batch(req: schemas.BatchScanRequest):
    """批量粘贴录入：每行一题；同题同答只归因一次，按学号展开成多条记录。

    解决两个真实痛点：① 一次测验批改后集中录 15–30 条典型错题，逐条填表单太慢；
    ② 一道共性错误多人错（如 20 人）——同一题只调一次大模型，展开成 20 条带学号的记录。
    """
    roster = {s["student_code"]: (s.get("name_local") or "") for s in db.list_students(req.class_name or "")}
    lines = [ln.strip() for ln in (req.text or "").splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(400, "没有可录入的行：请按「学号 | 题干 | 学生作答」格式粘贴后再提交")

    results = []
    ai_cache = {}  # (question, answer) → 归因结果：同题同答只调一次大模型
    for line_no, line in enumerate(lines, 1):
        codes, question, answer, correct, err = _parse_batch_line(line, roster)
        if err:
            results.append({"line_no": line_no, "ok": False, "msg": err, "raw": line[:60]})
            continue
        key = (question, answer)
        if key not in ai_cache:
            # 反标签化：归因 payload 只有题目本身，学号不进入任何模型请求
            ai_cache[key] = classifier.classify(question, answer, correct,
                                                req.qtype or "", passage=(req.passage or ""))
        ai = ai_cache[key]
        targets = codes or [""]  # 未关联学生 → 一条班级级记录
        for code in targets:
            behavior = None
            if code:
                behavior = classifier.review_behavior(code, ai["category_id"], question, req.class_name or "")
            db.insert_error({
                "student_code": code,
                "class_name": req.class_name or "",
                "question": question, "answer": answer, "correct": correct,
                "qtype": req.qtype or "", "passage": (req.passage or "") or None,
                "options": None, "unit": req.unit, "week": req.week,
                "exam_type": req.exam_type, "batch_no": req.batch_no,
                "category_id": ai["category_id"], "evidence": ai["evidence"],
                "teaching_point": ai["teaching_point"], "confidence": ai["confidence"],
                "needs_review": ai["needs_review"], "behavior": behavior,
            })
        results.append({"line_no": line_no, "ok": True, "question": question,
                        "category_id": ai["category_id"], "confidence": ai["confidence"],
                        "students": len(targets)})

    inserted = sum(r["students"] for r in results if r["ok"])
    return {"results": results, "needs_confirm": True,
            "stats": {"lines": len(lines), "inserted": inserted,
                      "llm_calls": len(ai_cache),
                      "failed": len(lines) - sum(1 for r in results if r["ok"])}}


@router.post("/errors/scan-photos")
def scan_photos(req: schemas.PhotoSplitRequest):
    """拍照切题第一步：多图 → 逐页 OCR → AI 切题 → 返回题目列表（仅预览，不入库）。

    解决真实场景：一张照片含多道题、多种题型（选择/判断/完形/阅读…），
    甚至跨页——逐页转录后由 LLM 按题号切分并合并跨页题。
    """
    images = [b for b in req.images_base64 if b]
    if not images:
        raise HTTPException(400, "请至少上传一张图片")
    if len(images) > 10:
        raise HTTPException(400, "一次最多 10 张（多页可分批）")

    texts = []
    for i, b64 in enumerate(images, 1):
        try:
            page_text = ocr.ocr_page(ocr.normalize_image(b64))
        except ocr.OCRError as exc:
            logger.warning("OCR 失败：%s", exc)
            raise HTTPException(400, "第 %d 张照片识别失败，请重拍或稍后重试。" % i)
        if not page_text.strip():
            raise HTTPException(400, "第 %d 张图片未识别到文字（请重拍：光线充足、镜头对正）" % i)
        texts.append("【第%d页】\n%s" % (i, page_text))

    questions = classifier.split_questions("\n\n".join(texts))
    if not questions:
        raise HTTPException(400, "切题失败：AI 未能从 %d 页文本中切出题目，"
                                 "请改用「单题录入」逐题上传，或让学生裁剪单题后发送" % len(texts))
    return {"pages": len(texts), "questions": questions,
            "note": "AI 生成 · 请逐题核对（可编辑/取消勾选）后提交"}


@router.post("/errors/scan-photos-submit")
def scan_photos_submit(req: schemas.PhotoSubmitRequest):
    """拍照切题第二步：提交勾选的题目 → 逐题归因 → 待确认入库。

    每题只调一次归因；students 多代号时展开成每人一条（同题去重归因）。
    """
    if not req.questions:
        raise HTTPException(400, "没有可提交的题目（请勾选至少一题）")
    roster = {s["student_code"] for s in db.list_students(req.class_name or "")}

    results = []
    ai_cache = {}  # (question, answer) → 归因结果：同题只调一次大模型
    inserted = 0
    for idx, q in enumerate(req.questions, 1):
        question = (q.question or "").strip()
        if not question:
            results.append({"no": idx, "ok": False, "msg": "题干为空，已跳过"})
            continue
        codes = [c.strip() for c in (q.students or []) if c and c.strip()]
        missing = [c for c in codes if c not in roster]
        if missing:
            results.append({"no": idx, "ok": False,
                            "msg": "学生代号 %s 不在本班名册——请先到「学生」页导入"
                                   % "、".join(missing), "question": question[:60]})
            continue
        key = (question, q.answer or "")
        if key not in ai_cache:
            # 反标签化：归因 payload 只有题目本身，学号不进入任何模型请求
            ai_cache[key] = classifier.classify(
                question, q.answer or "", q.correct or "", q.qtype or "",
                passage=(q.passage or ""))
        ai = ai_cache[key]
        targets = codes or [""]  # 未关联学生 → 一条班级级记录
        for code in targets:
            behavior = None
            if code:
                behavior = classifier.review_behavior(code, ai["category_id"],
                                                      question, req.class_name or "")
            db.insert_error({
                "student_code": code,
                "class_name": req.class_name or "",
                "question": question, "answer": q.answer, "correct": q.correct,
                "qtype": q.qtype or "", "passage": (q.passage or "") or None,
                "options": q.options, "unit": req.unit, "week": req.week,
                "exam_type": req.exam_type, "batch_no": req.batch_no,
                "category_id": ai["category_id"], "evidence": ai["evidence"],
                "teaching_point": ai["teaching_point"], "confidence": ai["confidence"],
                "needs_review": ai["needs_review"], "behavior": behavior,
            })
            inserted += 1
        results.append({"no": idx, "ok": True, "question": question,
                        "category_id": ai["category_id"],
                        "confidence": ai["confidence"], "students": len(targets)})

    return {"results": results, "needs_confirm": True,
            "stats": {"submitted": len(req.questions), "inserted": inserted,
                      "llm_calls": len(ai_cache),
                      "failed": sum(1 for r in results if not r["ok"])}}


@router.post("/errors/confirm-bulk")
def confirm_bulk(req: schemas.BulkConfirmRequest):
    """归因复核批量确认（减负）：待复核记录一键采纳/忽略。

    高置信度批量采纳 + 低置信度逐条复核——确认环节从逐题点击降为抽检；
    batch_no 可选：只作用于某次作业/考试的待确认记录。
    """
    if req.action not in ("accept", "ignore"):
        raise HTTPException(400, "批量确认仅支持 accept / ignore")
    try:
        n = db.confirm_errors_bulk(req.action, req.min_confidence, req.class_name,
                                   req.batch_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not n:
        raise HTTPException(400, "没有符合条件的待复核记录")
    return {"ok": True, "confirmed": n,
            "action": req.action,
            "min_confidence": req.min_confidence}


@router.get("/errors")
def list_errors(confirmed: Optional[bool] = None, unit: Optional[str] = None,
                student_code: Optional[str] = None,
                class_name: Optional[str] = None,
                exam_type: Optional[str] = None,
                batch_no: Optional[str] = None,
                limit: int = Query(500, le=2000)):
    return db.list_errors(confirmed=confirmed, unit=unit,
                          student_code=student_code, class_name=class_name,
                          exam_type=exam_type, batch_no=batch_no,
                          limit=limit)


@router.get("/errors/tags")
def error_tags(class_name: Optional[str] = None):
    """错题库中已存在的唯一标识列表（录入时从已有标识选择）。"""
    return {"tags": db.distinct_error_tags(class_name)}


@router.get("/errors/batches")
def error_batches(class_name: Optional[str] = None):
    """批次维度列表（某次作业/某次考试）：错题确认 / 错因分析 / 讲评备课共用。"""
    return {"batches": db.list_batches(class_name)}


@router.post("/errors/{error_id}/confirm")
def confirm(error_id: int, req: schemas.ConfirmRequest):
    """教师确认：采纳 / 改类（override 优先）/ 忽略。"""
    err = db.get_error(error_id)
    if not err:
        raise HTTPException(404, "错题 %d 不存在" % error_id)
    if req.action == "modify":
        if req.override and not ontology.get_category(req.override):
            raise HTTPException(400, "改判类别 %r 不在本体库中" % req.override)
    if req.action == "accept" and not err["category_id"]:
        raise HTTPException(400, "AI 未能归类，请选择类别后以【改类】方式确认")
    try:
        ok = db.confirm_error(error_id, req.action, req.override)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "错题 %d 不存在" % error_id)
    return {"ok": True, "error": db.get_error(error_id)}


# ---------------------------------------------------------------- 学生（反标签化）

@router.post("/students/import")
def import_students(req: schemas.StudentImportRequest):
    """名单导入：姓名只存本地映射表；出参仅代号。"""
    rows = req.rows or []
    if req.text:
        for line in req.text.splitlines():
            line = line.strip().replace(",", " ").replace("，", " ").replace("\t", " ")
            if not line:
                continue
            parts = line.split()
            rows.append({
                "code": parts[0],
                "name": parts[1] if len(parts) > 1 else "",
                "class": parts[2] if len(parts) > 2 else "",
            })
    if not rows:
        raise HTTPException(400, "名单为空")
    n = db.import_students(rows)
    return {"imported": n, "total": len(db.list_students())}


@router.get("/students")
def list_students(class_name: Optional[str] = None):
    """学生名册（教师本机视图：含姓名，仅用于帮扶对照；不出任何评价）。"""
    return db.list_students(class_name)


@router.post("/students")
def create_student(req: schemas.StudentCreateRequest):
    """单个新增学生（名册管理）。"""
    code = (req.student_code or "").strip()
    if not code:
        raise HTTPException(400, "学生代号不能为空")
    if db.get_student(code):
        raise HTTPException(400, "代号 %s 已存在（可点击名册中的 ✎ 直接编辑）" % code)
    db.upsert_student(code, (req.name_local or "").strip() or None,
                      (req.class_name or "").strip())
    return {"ok": True, "student_code": code}


@router.put("/students/{code}")
def update_student(code: str, req: schemas.StudentUpdateRequest):
    """修改学生：姓名 / 班级 / 代号（改代号时级联更新错题关联，不断链）。"""
    if not db.get_student(code):
        raise HTTPException(404, "学生不存在：%s" % code)
    try:
        ok = db.update_student(code,
                               name_local=req.name_local,
                               class_name=req.class_name,
                               new_code=(req.new_code or "").strip() or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.delete("/students/{code}")
def delete_student(code: str, purge_errors: bool = True):
    """删除名册学生及其全部错题（默认连同该生全部数据删除）；
    purge_errors=false 时保留错题（转为班级级记录）。"""
    if not db.get_student(code):
        raise HTTPException(404, "学生不存在：%s" % code)
    removed = db.delete_student(code, purge_errors=purge_errors)
    return {"ok": True, "removed_errors": removed,
            "mode": "purge" if purge_errors else "keep"}


@router.get("/students/{code}/profile")
def student_profile(code: str):
    """单个学生错因画像——教师个体帮扶视图。

    仅供任课教师本机查某个学生的错因分布与最近错题，用于面批准备；
    不生成评价性结论、不参与任何排名（反标签化边界内）。name_local 仅本机返回。
    """
    p = db.student_profile(code)
    if not p:
        raise HTTPException(404, "学生不存在：%s" % code)
    return p


@router.post("/students/{code}/cleanup")
def cleanup_student_history(code: str, req: schemas.StudentCleanupRequest):
    """学生历史数据清理：按维度删除该生的错题与上传照片登记。

    dimension：1w（一周前）/ 1m（一个月前）/ all（全部清除）/
    category（按错因类别，需 category_id）/ qtype（按题型，需 qtype）。
    班级批改历史（check_sessions）是班级级存档，不受单生清理影响。
    """
    if not db.get_student(code):
        raise HTTPException(404, "学生不存在：%s" % code)
    dim = (req.dimension or "").strip().lower()
    now = datetime.now()
    cutoff = None
    category_id = qtype = None
    if dim == "1w":
        cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
        label = "一周前"
    elif dim == "1m":
        cutoff = (now - timedelta(days=30)).isoformat(timespec="seconds")
        label = "一个月前"
    elif dim == "all":
        label = "全部"
    elif dim == "category":
        category_id = (req.category_id or "").strip()
        if not category_id:
            raise HTTPException(400, "按错因清理需要提供 category_id")
        label = "错因类别 %s" % category_id
    elif dim == "qtype":
        qtype = (req.qtype or "").strip()
        if not qtype:
            raise HTTPException(400, "按题型清理需要提供 qtype")
        label = "题型 %s" % qtype
    else:
        raise HTTPException(
            400, "未知清理维度：%s（可选 1w / 1m / all / category / qtype）" % dim)
    removed = db.cleanup_student_history(code, cutoff, category_id, qtype)
    return {"ok": True, "dimension": dim, "label": label, "removed": removed}


# ---------------------------------------------------------------- 练习（审校必需）

@router.post("/practice/generate")
def practice_generate(req: schemas.PracticeGenRequest):
    """生成练习：单类（旧参数，返回结构兼容）或多类一次生成（每类各成一套）。"""
    ids = req.category_ids or ([req.category_id] if req.category_id else [])
    try:
        result = practice.generate_batch(ids, req.n, req.class_name or "",
                                         date_from=req.date_from or "",
                                         date_to=req.date_to or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if len(result["generated"]) == 1:   # 单类：平铺旧字段，旧客户端零改动
        result.update(result["generated"][0])
    return result


@router.post("/practice/{practice_id}/approve")
def practice_approve(practice_id: int):
    if not practice.approve(practice_id):
        raise HTTPException(404, "练习 %d 不存在或已审校" % practice_id)
    return {"ok": True}


@router.get("/practices")
def list_practices(status: Optional[str] = None):
    return db.list_practices(status=status)


# ---------------------------------------------------------------- 组卷（审校必需）

@router.post("/paper/generate")
def paper_generate(req: schemas.PaperGenRequest):
    """AI 组卷：基于班级/任务类型已确认错题分布生成综合试卷（draft，审校后导出）。"""
    try:
        return paper.generate_paper(req.n, req.unit or "", req.class_name or "",
                                   (req.title or "").strip(),
                                   difficulty=req.difficulty or "",
                                   qtype_pref=req.qtype_pref or "",
                                   exam_type=req.exam_type or "",
                                   date_from=req.date_from or "",
                                   date_to=req.date_to or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/paper/{paper_id}/approve")
def paper_approve(paper_id: int):
    if not paper.approve(paper_id):
        raise HTTPException(404, "试卷 %d 不存在或已审校" % paper_id)
    return {"ok": True}


@router.get("/papers")
def list_papers(status: Optional[str] = None):
    return db.list_papers(status=status)


# ---------------------------------------------------------------- 决策与报告

@router.get("/decision-sheet")
def get_decision_sheet(unit: Optional[str] = None,
                       class_name: Optional[str] = None,
                       exam_type: Optional[str] = None,
                       batch_nos: Optional[str] = None,
                       date_from: Optional[str] = None,
                       date_to: Optional[str] = None):
    """讲评课决策单（确定性算法生成，并留快照）。

    exam_type 为任务类型维度（课时作业 / 考试试卷）；unit 为旧参数兼容保留；
    batch_nos 为批次多选（逗号分隔，如 "Unit 1,期中考试"）；
    date_from/date_to 为阶段范围（YYYY-MM-DD，录入时间口径）。
    """
    batches = [b.strip() for b in (batch_nos or "").split(",") if b.strip()]
    return decision.build_decision_sheet(unit=unit, class_name=class_name,
                                         exam_type=exam_type,
                                         batch_nos=batches or None,
                                         date_from=date_from or None,
                                         date_to=date_to or None)


@router.post("/decision-sheet/ai")
def decision_sheet_ai(req: schemas.AIStrategyRequest):
    """AI 讲评策略：规则决策单数据 → LLM 生成执行策略（教师终审后采用）。

    混合架构：数字与名单由确定性规则给出（不编造），切入/步骤/时间分配
    等策略性内容由大模型生成（灵活通用）；LLM 失败不影响规则版决策单。
    """
    sheet = decision.build_decision_sheet(unit=req.unit, class_name=req.class_name,
                                          exam_type=req.exam_type,
                                          batch_nos=req.batch_nos,
                                          date_from=req.date_from,
                                          date_to=req.date_to, save=False)
    try:
        strategy = decision_ai.generate_strategy(sheet)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"strategy": strategy, "ai_tag": "AI 生成 · 请您把关",
            "based_on": {"unit": req.unit, "class_name": req.class_name,
                         "total_confirmed": sheet["total_confirmed"],
                         "focus_count": len(sheet["focus"]),
                         "skip_count": len(sheet["skip"])}}


@router.get("/dashboard")
def get_dashboard(unit: Optional[str] = None, class_name: Optional[str] = None,
                  exam_type: Optional[str] = None,
                  batch_no: Optional[str] = None):
    return report.dashboard(unit, class_name, exam_type, batch_no=batch_no)


@router.get("/trends/categories")
def get_trends(by: Literal["week", "month", "half_year"] = "week",
               class_name: Optional[str] = None,
               batch_no: Optional[str] = None):
    """错因演变曲线：x 为录入错题的时间分桶（按周 / 按月 / 按半年）。

    batch_no 可选：只看某次作业/考试的错因演变。
    """
    return report.trends(by, class_name, batch_no=batch_no)


# ---------------------------------------------------------------- 导出

@router.get("/export/errors.csv")
def export_csv(class_name: Optional[str] = None):
    """CSV 导出（utf-8-sig 带 BOM，Excel 直开）。仅含代号，不含姓名。"""
    from fastapi.responses import PlainTextResponse

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "class_name", "exam_type", "batch_no", "student_code", "qtype",
                     "question", "answer", "correct", "final_category",
                     "ai_category", "teacher_action", "created_at"])
    for e in db.list_errors(limit=5000, class_name=class_name):
        writer.writerow([
            e["id"], e.get("class_name") or "", e.get("exam_type") or "",
            e.get("batch_no") or "",
            e["student_code"], e.get("qtype") or "", e["question"], (e.get("answer") or ""),
            (e.get("correct") or ""), e["final_category"], e["category_id"],
            e.get("teacher_action") or "", e["created_at"],
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8-sig")
