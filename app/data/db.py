"""SQLite 数据层：6 张表 + CRUD + 全部聚合查询（支持多班级）。

统计铁律：
  1. 教师修正优先——一切统计口径取 COALESCE(teacher_override, category_id)；
  2. teacher_action='ignore' 的记录不参与统计；
  3. 学生姓名仅存 students.name_local 本地映射，任何查询出参不带姓名（反标签化）。

多班级：students / errors 均带 class_name；一切聚合查询支持按班过滤，
默认 class_name=None 表示全部班级合并统计。
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..core import config

# 统计口径：教师改判优先于 AI 归因
FINAL_CATEGORY_SQL = "COALESCE(NULLIF(e.teacher_override,''), e.category_id)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code TEXT NOT NULL UNIQUE,
    name_local TEXT,
    class_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code TEXT NOT NULL,
    class_name TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT,
    correct TEXT,
    qtype TEXT,
    options TEXT,
    unit TEXT,
    week INTEGER,
    exam_type TEXT,
    batch_no TEXT,
    category_id TEXT NOT NULL,
    evidence TEXT,
    teaching_point TEXT,
    confidence REAL DEFAULT 0,
    needs_review INTEGER DEFAULT 0,
    behavior TEXT,
    teacher_action TEXT,
    teacher_override TEXT,
    session_id INTEGER,
    source TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_unit ON errors(unit);
CREATE INDEX IF NOT EXISTS idx_errors_student ON errors(student_code);
CREATE INDEX IF NOT EXISTS idx_errors_category ON errors(category_id);

CREATE TABLE IF NOT EXISTS practices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id TEXT NOT NULL,
    class_name TEXT NOT NULL DEFAULT '',
    items TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    created_at TEXT NOT NULL,
    approved_at TEXT,
    scope TEXT
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    class_name TEXT NOT NULL DEFAULT '',
    unit TEXT,
    scope TEXT,
    items TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    created_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS decision_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    focus_categories TEXT,
    skip_categories TEXT,
    tutor_list TEXT,
    practice_ids TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT,
    class_name TEXT NOT NULL DEFAULT '',
    unit TEXT,
    week INTEGER,
    exam_type TEXT,
    batch_no TEXT,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_check_sessions_kind ON check_sessions(kind);

CREATE TABLE IF NOT EXISTS paper_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    class_name TEXT NOT NULL DEFAULT '',
    batch_no TEXT,
    student_code TEXT DEFAULT '',
    student_name TEXT DEFAULT '',
    file_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_files_session ON paper_files(session_id);
"""

# 旧库迁移：2.x 之前的库无 class_name 列，启动时自动补齐
_MIGRATIONS = [
    ("students", "class_name", "ALTER TABLE students ADD COLUMN class_name TEXT NOT NULL DEFAULT ''"),
    ("errors", "class_name", "ALTER TABLE errors ADD COLUMN class_name TEXT NOT NULL DEFAULT ''"),
    ("practices", "class_name", "ALTER TABLE practices ADD COLUMN class_name TEXT NOT NULL DEFAULT ''"),
    ("errors", "passage", "ALTER TABLE errors ADD COLUMN passage TEXT"),
    ("check_sessions", "exam_type", "ALTER TABLE check_sessions ADD COLUMN exam_type TEXT"),
    ("errors", "session_id", "ALTER TABLE errors ADD COLUMN session_id INTEGER"),
    ("errors", "source", "ALTER TABLE errors ADD COLUMN source TEXT"),
    ("check_sessions", "batch_no", "ALTER TABLE check_sessions ADD COLUMN batch_no TEXT"),
    ("practices", "scope", "ALTER TABLE practices ADD COLUMN scope TEXT"),
    ("errors", "exam_type", "ALTER TABLE errors ADD COLUMN exam_type TEXT"),
    ("errors", "batch_no", "ALTER TABLE errors ADD COLUMN batch_no TEXT"),
]

# 批改类型归并：三段式命名规范只保留「课时作业 / 考试试卷」两类，
# 旧库七值（单元测试/月考/期中测试/期末测试/其他/日常作业/听写）启动时一次性归并
_EXAM_TYPE_MERGE = {
    "考试试卷": ("单元测试", "月考", "期中测试", "期末测试", "其他"),
    "课时作业": ("日常作业", "听写"),
}


# 新 schema 索引（依赖迁移列，建表后单独执行）
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_errors_class ON errors(class_name);
CREATE INDEX IF NOT EXISTS idx_errors_session ON errors(session_id);
"""

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    db_path = config.get_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        # 旧库迁移：先补列，再归并批改类型，最后建依赖新列的索引
        for table, column, ddl in _MIGRATIONS:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
            if column not in cols:
                conn.execute(ddl)
        for new_type, old_types in _EXAM_TYPE_MERGE.items():
            conn.execute(
                "UPDATE check_sessions SET exam_type=? WHERE exam_type IN (%s)"
                % ",".join("?" * len(old_types)),
                (new_type, *old_types))
        # 二改功能已移除：清理遗留表 revisions（含指向 errors 的外键，会阻塞级联删除）
        conn.execute("DROP TABLE IF EXISTS revisions")
        conn.executescript(_INDEXES)


# ---------------------------------------------------------------- students

def upsert_student(student_code: str, name_local: Optional[str] = None,
                   class_name: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO students(student_code, name_local, class_name, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(student_code) DO UPDATE SET "
            "name_local=COALESCE(excluded.name_local, students.name_local), "
            "class_name=CASE WHEN excluded.class_name!='' THEN excluded.class_name "
            "ELSE students.class_name END",
            (student_code, name_local, class_name or "", _now()),
        )


def import_students(rows: List[Dict[str, Any]]) -> int:
    """rows: [{code, name, class}]。姓名只进本地库，接口出参永不返回。"""
    n = 0
    with get_conn() as conn:
        for r in rows:
            code = (r.get("code") or "").strip()
            if not code:
                continue
            conn.execute(
                "INSERT INTO students(student_code, name_local, class_name, created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(student_code) DO UPDATE SET name_local=excluded.name_local, "
                "class_name=CASE WHEN excluded.class_name!='' THEN excluded.class_name "
                "ELSE students.class_name END",
                (code, (r.get("name") or "").strip() or None,
                 (r.get("class") or "").strip(), _now()),
            )
            n += 1
    return n


def list_students(class_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """学生名册（教师本机视图：含姓名，供帮扶对照；不产生任何评价）。"""
    sql = "SELECT student_code, name_local, class_name, created_at FROM students"
    args: Tuple = ()
    if class_name:
        sql += " WHERE class_name=?"
        args = (class_name,)
    sql += " ORDER BY class_name, student_code"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_student(student_code: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT student_code, name_local, class_name, created_at FROM students WHERE student_code=?",
            (student_code,)).fetchone()
    return dict(r) if r else None


def update_student(student_code: str, name_local: Optional[str] = None,
                   class_name: Optional[str] = None,
                   new_code: Optional[str] = None) -> bool:
    """修改学生（姓名 / 班级 / 代号）；改代号时级联更新 errors 关联，不断链。"""
    with get_conn() as conn:
        row = conn.execute("SELECT student_code FROM students WHERE student_code=?",
                           (student_code,)).fetchone()
        if not row:
            return False
        if new_code and new_code != student_code:
            dup = conn.execute("SELECT 1 FROM students WHERE student_code=?", (new_code,)).fetchone()
            if dup:
                raise ValueError("新代号 %s 已被其他学生占用" % new_code)
            conn.execute("UPDATE students SET student_code=? WHERE student_code=?",
                         (new_code, student_code))
            conn.execute("UPDATE errors SET student_code=? WHERE student_code=?",
                         (new_code, student_code))
            student_code = new_code
        if name_local is not None:
            conn.execute("UPDATE students SET name_local=? WHERE student_code=?",
                         ((name_local or "").strip() or None, student_code))
        if class_name is not None:
            conn.execute("UPDATE students SET class_name=? WHERE student_code=?",
                         ((class_name or "").strip(), student_code))
    return True


def _purge_revision_refs(conn, ids_sql: str, args: tuple) -> None:
    """删除二改遗留表 revisions 中对指定错题的引用（旧库外键会阻塞级联删除）。

    revisions 表在启动迁移中已 DROP；此处防御处理未重启进程期间的老库。
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='revisions'").fetchone()
    if has:
        conn.execute("DELETE FROM revisions WHERE error_id IN (%s)" % ids_sql, args)


def delete_student(student_code: str, purge_errors: bool = True) -> int:
    """删除名册学生及其全部数据（错题 + 上传照片登记）。

    默认 purge_errors=True：连同该生全部错题与照片登记一并删除；
    purge_errors=False 时保留错题（置为班级级记录）。返回受影响的错题数。
    """
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM errors WHERE student_code=?",
                         (student_code,)).fetchone()["c"]
        if purge_errors:
            _purge_revision_refs(
                conn, "SELECT id FROM errors WHERE student_code=?", (student_code,))
            conn.execute("DELETE FROM errors WHERE student_code=?", (student_code,))
            conn.execute("DELETE FROM paper_files WHERE student_code=?", (student_code,))
        else:
            conn.execute("UPDATE errors SET student_code='' WHERE student_code=?", (student_code,))
        conn.execute("DELETE FROM students WHERE student_code=?", (student_code,))
    return n


def cleanup_student_history(student_code: str,
                            cutoff: Optional[str] = None,
                            category_id: Optional[str] = None,
                            qtype: Optional[str] = None) -> Dict[str, int]:
    """按维度清理学生的历史数据（错题 + 上传照片登记）。

    支持条件组合：
    - cutoff：只删 created_at 早于该时刻的记录（None=不限时间）；
    - category_id / qtype：只删指定错因类别 / 题型的错题。
    照片登记（paper_files）只有时间维度：仅在未指定错因/题型条件时
    跟随 cutoff 清理，按错因/题型清理时不触碰照片登记。
    班级批改历史（check_sessions）是班级级存档，不在单生清理范围内。
    返回 {"errors": n, "paper_files": m}。
    """
    conds = ["student_code=?"]
    args: List[Any] = [student_code]
    if cutoff:
        conds.append("created_at<?")
        args.append(cutoff)
    if category_id:
        conds.append("category_id=?")
        args.append(category_id)
    if qtype:
        conds.append("qtype=?")
        args.append(qtype)
    where = " WHERE " + " AND ".join(conds)
    with get_conn() as conn:
        _purge_revision_refs(conn, "SELECT id FROM errors" + where, tuple(args))
        n = conn.execute("DELETE FROM errors" + where, tuple(args)).rowcount
        m = 0
        if not category_id and not qtype:   # 照片登记只随时间维度清理
            if cutoff:
                m = conn.execute(
                    "DELETE FROM paper_files WHERE student_code=? AND created_at<?",
                    (student_code, cutoff)).rowcount
            else:
                m = conn.execute(
                    "DELETE FROM paper_files WHERE student_code=?", (student_code,)).rowcount
    return {"errors": n, "paper_files": m}


def list_classes() -> List[str]:
    """已录的班级列表：classes 表（显式创建的班级）+ 学生/错题中出现过的班级。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM classes "
            "UNION SELECT DISTINCT class_name FROM students WHERE class_name!='' "
            "UNION SELECT DISTINCT class_name FROM errors WHERE class_name!='' "
            "ORDER BY 1"
        ).fetchall()
    return [r["name"] for r in rows]


def add_class(name: str) -> None:
    """新建班级（显式创建，可先建班再录学生/错题）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO classes(name, created_at) VALUES(?,?) "
            "ON CONFLICT(name) DO NOTHING", (name, _now()))


def rename_class(old: str, new: str) -> bool:
    """班级改名：classes 表 + 级联 students / errors 的 class_name，不断链。"""
    with get_conn() as conn:
        conn.execute("UPDATE classes SET name=? WHERE name=?", (new, old))
        conn.execute("UPDATE students SET class_name=? WHERE class_name=?", (new, old))
        conn.execute("UPDATE errors SET class_name=? WHERE class_name=?", (new, old))
    return True


def delete_class(name: str) -> Tuple[int, int]:
    """删除班级及该班全部数据（学生 / 错题 / 补偿练习 / 组卷 / 批改记录 / 照片登记）。
    返回 (student_count, error_count) 供前端确认提示。"""
    with get_conn() as conn:
        stu = conn.execute("SELECT COUNT(*) c FROM students WHERE class_name=?",
                           (name,)).fetchone()["c"]
        err = conn.execute("SELECT COUNT(*) c FROM errors WHERE class_name=?",
                           (name,)).fetchone()["c"]
        _purge_revision_refs(
            conn, "SELECT id FROM errors WHERE class_name=?", (name,))
        conn.execute("DELETE FROM errors WHERE class_name=?", (name,))
        conn.execute("DELETE FROM paper_files WHERE class_name=?", (name,))
        conn.execute("DELETE FROM check_sessions WHERE class_name=?", (name,))
        conn.execute("DELETE FROM practices WHERE class_name=?", (name,))
        conn.execute("DELETE FROM papers WHERE class_name=?", (name,))
        conn.execute("DELETE FROM students WHERE class_name=?", (name,))
        conn.execute("DELETE FROM classes WHERE name=?", (name,))
    return stu, err


def class_size(class_name: Optional[str] = None) -> int:
    with get_conn() as conn:
        if class_name:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM students WHERE class_name=?",
                (class_name,)).fetchone()["c"]
        return conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]


# ---------------------------------------------------------------- errors

def insert_error(rec: Dict[str, Any]) -> int:
    cols = {
        "student_code": rec.get("student_code") or "",
        "class_name": rec.get("class_name") or "",
        "question": rec.get("question") or "",
        "answer": rec.get("answer"),
        "correct": rec.get("correct"),
        "qtype": rec.get("qtype"),
        "passage": rec.get("passage"),
        "options": json.dumps(rec["options"], ensure_ascii=False) if rec.get("options") else None,
        "unit": rec.get("unit"),
        "week": rec.get("week"),
        "exam_type": rec.get("exam_type"),
        "batch_no": rec.get("batch_no"),
        "category_id": rec["category_id"],
        "evidence": rec.get("evidence"),
        "teaching_point": rec.get("teaching_point"),
        "confidence": rec.get("confidence", 0.0),
        "needs_review": 1 if rec.get("needs_review") else 0,
        "behavior": json.dumps(rec["behavior"], ensure_ascii=False) if rec.get("behavior") else None,
        "session_id": rec.get("session_id"),
        "source": rec.get("source"),
        "created_at": rec.get("created_at") or _now(),
    }
    sql = "INSERT INTO errors({}) VALUES({})".format(
        ",".join(cols.keys()), ",".join("?" * len(cols))
    )
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(cols.values()))
        return cur.lastrowid


def _row_to_error(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    if d.get("options"):
        try:
            d["options"] = json.loads(d["options"])
        except (ValueError, TypeError):
            pass
    if d.get("behavior"):
        try:
            d["behavior"] = json.loads(d["behavior"])
        except (ValueError, TypeError):
            pass
    else:
        d["behavior"] = None
    # 统一暴露最终口径类别（教师改判优先）
    d["final_category"] = d.get("teacher_override") or d.get("category_id")
    d["confirmed"] = d.get("teacher_action") is not None
    return d


_ERROR_COLS = ("id, student_code, class_name, question, answer, correct, qtype, options, passage, "
               "unit, week, exam_type, batch_no, category_id, evidence, teaching_point, confidence, "
               "needs_review, behavior, teacher_action, teacher_override, "
               "created_at, confirmed_at, session_id, source")


def get_error(error_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT {} FROM errors WHERE id=?".format(_ERROR_COLS), (error_id,)
        ).fetchone()
    return _row_to_error(r) if r else None


def list_errors(
    confirmed: Optional[bool] = None,
    unit: Optional[str] = None,
    student_code: Optional[str] = None,
    class_name: Optional[str] = None,
    exam_type: Optional[str] = None,
    batch_no: Optional[str] = None,
    batch_nos: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    category_id: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    sql = "SELECT {} FROM errors WHERE 1=1".format(_ERROR_COLS)
    args: List[Any] = []
    if confirmed is True:
        sql += " AND teacher_action IS NOT NULL"
    elif confirmed is False:
        sql += " AND teacher_action IS NULL"
    if unit:
        # 旧版 unit 参数兼容：新数据唯一标识存 batch_no，旧数据存 unit
        sql += " AND COALESCE(NULLIF(batch_no, ''), unit)=?"
        args.append(unit)
    if batch_nos:
        sql += " AND COALESCE(NULLIF(batch_no, ''), unit) IN (%s)" \
            % ",".join("?" * len(batch_nos))
        args.extend(batch_nos)
    d_conds, d_args = _date_bounds(date_from, date_to)
    for c in d_conds:
        sql += " AND " + c.replace("e.", "")
    args.extend(d_args)
    if student_code:
        sql += " AND student_code=?"
        args.append(student_code)
    if class_name:
        sql += " AND class_name=?"
        args.append(class_name)
    if exam_type:
        sql += " AND exam_type=?"
        args.append(exam_type)
    if batch_no:
        sql += " AND batch_no=?"
        args.append(batch_no)
    if category_id:
        # 按最终错因筛（教师改判优先口径，与统计一致）：练习生成取真实错例用
        sql += " AND {}=?".format(FINAL_CATEGORY_SQL.replace("e.", ""))
        args.append(category_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_row_to_error(r) for r in rows]


def distinct_error_tags(class_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """错题库中已存在的唯一标识列表（供录入时从已有标识选择）。

    返回 [{batch_no, exam_type, count}]，按最近录入优先；
    同时包含批改历史（check_sessions）中登记过的标识（无错题但有标识的场景）。
    """
    tags: Dict[Tuple, Dict[str, Any]] = {}
    with get_conn() as conn:
        sql = ("SELECT batch_no, exam_type, COUNT(*) AS count, MAX(created_at) AS last_at "
               "FROM errors WHERE batch_no IS NOT NULL AND batch_no != ''")
        args: List[Any] = []
        if class_name:
            sql += " AND class_name=?"
            args.append(class_name)
        sql += " GROUP BY batch_no, exam_type"
        for r in conn.execute(sql, tuple(args)).fetchall():
            tags[(r["batch_no"], r["exam_type"] or "")] = {
                "batch_no": r["batch_no"], "exam_type": r["exam_type"] or "",
                "count": r["count"], "last_at": r["last_at"] or "",
            }
        sql2 = ("SELECT batch_no, exam_type, MAX(created_at) AS last_at "
                "FROM check_sessions WHERE batch_no IS NOT NULL AND batch_no != ''")
        args2: List[Any] = []
        if class_name:
            sql2 += " AND class_name=?"
            args2.append(class_name)
        sql2 += " GROUP BY batch_no, exam_type"
        for r in conn.execute(sql2, tuple(args2)).fetchall():
            key = (r["batch_no"], r["exam_type"] or "")
            if key not in tags:
                tags[key] = {
                    "batch_no": r["batch_no"], "exam_type": r["exam_type"] or "",
                    "count": 0, "last_at": r["last_at"] or "",
                }
    return sorted(tags.values(), key=lambda t: t["last_at"], reverse=True)


def list_batches(class_name: Optional[str] = None,
                  limit: int = 200) -> List[Dict[str, Any]]:
    """批次维度列表（某次作业/某次考试）：错题库实际存在的批次聚合。

    供错题确认 / 错因分析 / 讲评备课的批次筛选下拉共用；
    batch_no 新旧兼容（batch_no 优先，旧数据回落 unit），
    按最近录入优先；count=总条数、confirmed=已确认条数。
    """
    sql = (
        "SELECT COALESCE(NULLIF(batch_no, ''), unit) AS bno, exam_type, "
        "COUNT(*) AS count, "
        "SUM(CASE WHEN teacher_action IS NOT NULL "
        "          AND teacher_action != 'ignore' THEN 1 ELSE 0 END) AS confirmed, "
        "MIN(created_at) AS first_at, MAX(created_at) AS last_at "
        "FROM errors "
        "WHERE COALESCE(NULLIF(batch_no, ''), unit) IS NOT NULL "
        "  AND COALESCE(NULLIF(batch_no, ''), unit) != ''"
    )
    args: List[Any] = []
    if class_name:
        sql += " AND class_name=?"
        args.append(class_name)
    sql += " GROUP BY bno, exam_type ORDER BY last_at DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [{
        "batch_no": r["bno"],
        "exam_type": r["exam_type"] or "",
        "count": r["count"],
        "confirmed": r["confirmed"] or 0,
        "first_at": r["first_at"] or "",
        "last_at": r["last_at"] or "",
    } for r in rows]


def confirm_error(error_id: int, action: str, override: Optional[str] = None,
                  confirmed_at: Optional[str] = None) -> bool:
    """action: accept / modify / ignore。modify 时 override 必填且必须来自本体库。"""
    if action not in ("accept", "modify", "ignore"):
        raise ValueError("action 必须是 accept/modify/ignore")
    if action == "modify" and not override:
        raise ValueError("改类（modify）必须提供 override 类别")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE errors SET teacher_action=?, teacher_override=?, confirmed_at=? WHERE id=?",
            (action, override if action == "modify" else None,
             confirmed_at or _now(), error_id),
        )
        return cur.rowcount > 0


def confirm_errors_bulk(action: str, min_confidence: Optional[float] = None,
                        class_name: Optional[str] = None,
                        batch_no: Optional[str] = None) -> int:
    """批量确认全部待复核记录（减负）：accept / ignore，返回影响行数。

    只作用于 teacher_action IS NULL 的记录；min_confidence 仅 accept 时生效
    （如 0.8 = 只采纳高置信度，其余留给教师逐条复核）；
    batch_no 可选：只作用于某次作业/考试的待确认记录。
    """
    if action not in ("accept", "ignore"):
        raise ValueError("批量确认 action 必须是 accept/ignore")
    sql = ("UPDATE errors SET teacher_action=?, teacher_override=NULL, "
           "confirmed_at=? WHERE teacher_action IS NULL")
    args: List[Any] = [action, _now()]
    if action == "accept" and min_confidence is not None:
        sql += " AND confidence >= ?"
        args.append(min_confidence)
    if class_name:
        sql += " AND class_name=?"
        args.append(class_name)
    if batch_no:
        sql += " AND COALESCE(NULLIF(batch_no, ''), unit)=?"
        args.append(batch_no)
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(args))
        return cur.rowcount


# ---------------------------------------------------------------- practices

def insert_practice(category_id: str, items: List[Dict[str, Any]],
                    class_name: str = "", scope: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO practices(category_id, class_name, items, status, created_at, scope) "
            "VALUES(?,?,?,?,?,?)",
            (category_id, class_name or "", json.dumps(items, ensure_ascii=False),
             "draft", _now(), scope or ""),
        )
        return cur.lastrowid


def approve_practice(practice_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE practices SET status='approved', approved_at=? WHERE id=? AND status='draft'",
            (_now(), practice_id),
        )
        return cur.rowcount > 0


def list_practices(status: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = ("SELECT id, category_id, class_name, items, status, created_at, approved_at, scope "
           "FROM practices")
    args: Tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY id DESC"
    out: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for r in conn.execute(sql, args).fetchall():
            d = dict(r)
            try:
                d["items"] = json.loads(d["items"])
            except (ValueError, TypeError):
                d["items"] = []
            out.append(d)
    return out


def get_practice(practice_id: int) -> Optional[Dict[str, Any]]:
    rows = [p for p in list_practices() if p["id"] == practice_id]
    return rows[0] if rows else None


# ---------------------------------------------------------------- papers（AI 组卷）

def insert_paper(title: str, items: List[Dict[str, Any]], class_name: str = "",
                 unit: str = "", scope: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO papers(title, class_name, unit, scope, items, status, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (title or "", class_name or "", unit or "", scope or "",
             json.dumps(items, ensure_ascii=False), "draft", _now()),
        )
        return cur.lastrowid


def approve_paper(paper_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE papers SET status='approved', approved_at=? WHERE id=? AND status='draft'",
            (_now(), paper_id),
        )
        return cur.rowcount > 0


def list_papers(status: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT id, title, class_name, unit, scope, items, status, created_at, approved_at FROM papers"
    args: Tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY id DESC"
    out: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for r in conn.execute(sql, args).fetchall():
            d = dict(r)
            try:
                d["items"] = json.loads(d["items"])
            except (ValueError, TypeError):
                d["items"] = []
            out.append(d)
    return out


def get_paper(paper_id: int) -> Optional[Dict[str, Any]]:
    rows = [p for p in list_papers() if p["id"] == paper_id]
    return rows[0] if rows else None


def student_check_scores(class_name: Optional[str] = None) -> Dict[str, List[float]]:
    """每名学生的历次批改得分（教师本机汇总导出用；只聚合分数，不做任何排名）。"""
    sql = "SELECT report FROM check_sessions"
    args: Tuple = ()
    if class_name:
        sql += " WHERE class_name=?"
        args = (class_name,)
    out: Dict[str, List[float]] = {}
    with get_conn() as conn:
        for r in conn.execute(sql, args).fetchall():
            try:
                report = json.loads(r["report"]) if r["report"] else {}
            except (ValueError, TypeError):
                continue
            for s in report.get("results") or []:
                code = s.get("student_code") or ""
                if code and s.get("score") is not None:
                    out.setdefault(code, []).append(float(s["score"]))
    return out


# ---------------------------------------------------------------- decision sheets

def save_decision_sheet(payload: Dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO decision_sheets(scope, focus_categories, skip_categories, "
            "tutor_list, practice_ids, created_at) VALUES(?,?,?,?,?,?)",
            (
                payload.get("scope", ""),
                json.dumps(payload.get("focus", []), ensure_ascii=False),
                json.dumps(payload.get("skip", []), ensure_ascii=False),
                json.dumps(payload.get("tutors", []), ensure_ascii=False),
                json.dumps(payload.get("practice_ids", []), ensure_ascii=False),
                _now(),
            ),
        )
        return cur.lastrowid


def list_decision_sheets(limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM decision_sheets ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k, jk in (("focus", "focus_categories"), ("skip", "skip_categories"),
                      ("tutors", "tutor_list"), ("practice_ids", "practice_ids")):
            try:
                d[k] = json.loads(d[jk] or "[]")
            except (ValueError, TypeError):
                d[k] = []
        out.append(d)
    return out


# ---------------------------------------------------------------- 批改记录（听写/作业批改）

def insert_check_session(kind: str, title: Optional[str], class_name: str,
                         unit: Optional[str], week: Optional[int],
                         exam_type: Optional[str],
                         report: Dict[str, Any],
                         batch_no: Optional[str] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO check_sessions(kind, title, class_name, unit, week, "
            "exam_type, batch_no, report, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, title or "", class_name or "", unit, week, exam_type,
             batch_no, json.dumps(report, ensure_ascii=False), _now()),
        )
        return cur.lastrowid


def update_check_session(session_id: int, title: Optional[str] = None,
                         class_name: Optional[str] = None,
                         unit: Optional[str] = None, week: Optional[int] = None,
                         exam_type: Optional[str] = None,
                         batch_no: Optional[str] = None) -> bool:
    """手动修改批改记录的班级/单元/周次/批改类型/唯一标识/标题（改库内字段与报告 meta）。"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT report FROM check_sessions WHERE id=?", (session_id,)).fetchone()
        if not r:
            return False
        report = json.loads(r["report"]) if r["report"] else {}
        meta = report.setdefault("meta", {})
        if title is not None:
            report["title"] = title
        if class_name is not None:
            meta["class_name"] = class_name or ""
        if unit is not None:
            meta["unit"] = unit or None
        if week is not None:
            meta["week"] = week or None   # 0 / 空 = 清空
        if exam_type is not None:
            meta["exam_type"] = exam_type or None
        if batch_no is not None:
            meta["batch_no"] = batch_no or None
        conn.execute(
            "UPDATE check_sessions SET title=?, class_name=?, unit=?, week=?, "
            "exam_type=?, batch_no=?, report=? WHERE id=?",
            (report.get("title") or "", meta.get("class_name") or "",
             meta.get("unit"), meta.get("week"), meta.get("exam_type"),
             meta.get("batch_no"),
             json.dumps(report, ensure_ascii=False), session_id))
        return True


def _session_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    """历史列表行：从 report 里取概要数字（不返回全量明细）。"""
    r = d.get("report") or {}
    stats = r.get("stats") or {}
    return {
        "id": d["id"],
        "kind": d["kind"],
        "title": d.get("title") or "",
        "class_name": d.get("class_name") or "",
        "unit": d.get("unit"),
        "week": d.get("week"),
        "exam_type": d.get("exam_type"),
        "batch_no": d.get("batch_no"),
        "created_at": d.get("created_at") or "",
        "students": stats.get("students", 0),
        "avg_score": stats.get("avg_score"),
        "wrong_items": stats.get("wrong_items", 0),
        "uncertain": stats.get("uncertain", 0),
        "inserted_errors": stats.get("inserted_errors", 0),
    }


def list_check_sessions(limit: int = 20,
                        kind: Optional[str] = None,
                        exam_type: Optional[str] = None,
                        class_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """历史列表（概要数字，不含全量明细）。

    exam_type 过滤用于作业/考试两页隔离："考试试卷" 精确匹配；
    其他值（课时作业页）取非考试的一切（含 NULL/空与旧值），与前端页面归属一致；
    class_name 跟随批改页右上角班级过滤。
    """
    sql = "SELECT id, kind, title, class_name, unit, week, exam_type, batch_no, " \
          "report, created_at FROM check_sessions"
    conds, args = [], []
    if kind:
        conds.append("kind=?")
        args.append(kind)
    if class_name:
        conds.append("class_name=?")
        args.append(class_name)
    if exam_type:
        if exam_type == "考试试卷":
            conds.append("exam_type=?")
            args.append(exam_type)
        else:   # 作业页：非考试的一切（NULL/空/旧值都归作业页）
            conds.append("(exam_type IS NULL OR exam_type!='考试试卷')")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    args = tuple(args) + (limit,)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["report"] = json.loads(d["report"])
        except (ValueError, TypeError):
            d["report"] = {}
        out.append(_session_summary(d))
    return out


def get_check_session(session_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id, kind, title, class_name, unit, week, exam_type, batch_no, "
            "report, created_at FROM check_sessions WHERE id=?", (session_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["report"] = json.loads(d["report"])
    except (ValueError, TypeError):
        d["report"] = {}
    d["summary"] = _session_summary(d)
    return d


def delete_check_session(session_id: int, purge_errors: bool = False) -> Optional[int]:
    """删除一条批改历史（check_sessions + paper_files 照片登记）；

    默认保留本次收集的错题（errors.session_id 置 NULL，数据不丢，
    仅解除与本次批改的反查关联）；purge_errors=True 时连错题一并删除。
    返回受影响的错题数；记录不存在返回 None。
    """
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM check_sessions WHERE id=?",
                            (session_id,)).fetchone():
            return None
        n = conn.execute("SELECT COUNT(*) AS c FROM errors WHERE session_id=?",
                         (session_id,)).fetchone()["c"]
        if purge_errors:
            conn.execute("DELETE FROM errors WHERE session_id=?", (session_id,))
        else:
            conn.execute("UPDATE errors SET session_id=NULL WHERE session_id=?",
                         (session_id,))
        conn.execute("DELETE FROM paper_files WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM check_sessions WHERE id=?", (session_id,))
    return n


# ---------------------------------------------------------------- 上传文件登记（paper_files）

def insert_paper_files(rows: List[Dict[str, Any]]) -> int:
    """上传文件登记：批改提交时写入该批次全部照片的元数据（不存图片本体）。

    rows: [{session_id, class_name, batch_no, student_code, student_name,
            file_name}]，file_name 为相对批次根目录的「学生/文件名」。
    """
    n = 0
    with get_conn() as conn:
        for r in rows:
            file_name = (r.get("file_name") or "").strip()
            if not file_name:
                continue
            conn.execute(
                "INSERT INTO paper_files(session_id, class_name, batch_no, "
                "student_code, student_name, file_name, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (r.get("session_id"), r.get("class_name") or "",
                 r.get("batch_no"), r.get("student_code") or "",
                 r.get("student_name") or "", file_name, _now()),
            )
            n += 1
    return n


def list_paper_files(session_id: int) -> List[Dict[str, Any]]:
    """某次批改登记的全部上传文件（按学生、文件名排序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, session_id, class_name, batch_no, student_code, "
            "student_name, file_name, created_at FROM paper_files "
            "WHERE session_id=? ORDER BY student_name, file_name",
            (session_id,)).fetchall()
    return [dict(r) for r in rows]


def set_errors_session(error_ids: List[int], session_id: int) -> int:
    """批改记录建立后回填错题归属：一次提交可反查其全部错题（errors.session_id）。"""
    ids = [i for i in (error_ids or []) if i]
    if not ids:
        return 0
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE errors SET session_id=? WHERE id IN (%s)"
            % ",".join("?" * len(ids)),
            (session_id, *ids))
        return cur.rowcount


# ---------------------------------------------------------------- 聚合查询

def _batch_in_sql(batch_nos: List[str]) -> str:
    """批次多选的 IN 占位（新旧数据兼容：batch_no 优先，旧数据回落 unit）。"""
    return "COALESCE(NULLIF(e.batch_no, ''), e.unit) IN (%s)" % ",".join("?" * len(batch_nos))


def _date_bounds(date_from: Optional[str],
                 date_to: Optional[str]) -> Tuple[List[str], List[str]]:
    """阶段范围 → SQL 条件与参数（created_at 为 ISO 本地时间字符串）。

    date_to 取闭区间到当天末：字符串比较下 "2026-06-30T20:15" < "2026-07-01"
    恒成立（且不会误含次日），故上界用次日零点而不是拼接 23:59:59
    （ISO 时间含字母 T，直接与带空格的串比较会错序）。
    """
    conds: List[str] = []
    args: List[str] = []
    if date_from:
        conds.append("e.created_at >= ?")
        args.append(date_from)
    if date_to:
        try:
            end = (datetime.strptime(date_to[:10], "%Y-%m-%d")
                   + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            end = date_to
        conds.append("e.created_at < ?")
        args.append(end)
    return conds, args


def _confirmed_filter(where: List[str], args: List[Any], unit: Optional[str],
                      class_name: Optional[str],
                      exam_type: Optional[str] = None,
                      batch_nos: Optional[List[str]] = None,
                      date_from: Optional[str] = None,
                      date_to: Optional[str] = None) -> None:
    """统计公共过滤：已确认（非 ignore）+ 可选单元/班级/任务类型/批次/阶段。"""
    where.append("e.teacher_action IS NOT NULL AND e.teacher_action != 'ignore'")
    if unit:
        # 旧版 unit 参数兼容：新数据唯一标识存 batch_no，旧数据存 unit
        where.append("COALESCE(NULLIF(e.batch_no, ''), e.unit) = ?")
        args.append(unit)
    if batch_nos:
        where.append(_batch_in_sql(batch_nos))   # 某一次/某几次作业或考试
        args.extend(batch_nos)
    if class_name:
        where.append("e.class_name = ?")
        args.append(class_name)
    if exam_type:
        where.append("e.exam_type = ?")
        args.append(exam_type)
    d_conds, d_args = _date_bounds(date_from, date_to)   # 某个阶段（录入时间范围）
    where.extend(d_conds)
    args.extend(d_args)


def category_stats(unit: Optional[str] = None,
                   class_name: Optional[str] = None,
                   exam_type: Optional[str] = None,
                   batch_nos: Optional[List[str]] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None) -> List[Dict[str, Any]]:
    """教师确认后的错因分布（人次），教师改判优先。"""
    where: List[str] = []
    args: List[Any] = []
    _confirmed_filter(where, args, unit, class_name, exam_type,
                      batch_nos=batch_nos, date_from=date_from, date_to=date_to)
    sql = (
        "SELECT {fc} AS final_cat, COUNT(*) AS count "
        "FROM errors e WHERE {where} GROUP BY final_cat ORDER BY count DESC, final_cat"
    ).format(fc=FINAL_CATEGORY_SQL, where=" AND ".join(where))
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [{"category_id": r["final_cat"], "count": r["count"]} for r in rows]


# 趋势时间分桶（录入错题时间口径）：(排序键 SQL, 展示标签 SQL)
_BUCKET_SQL = {
    "week": (
        "strftime('%Y', e.created_at) || printf('%02d', CAST(strftime('%W', e.created_at) AS INTEGER) + 1)",
        "strftime('%Y', e.created_at) || '-第' || (CAST(strftime('%W', e.created_at) AS INTEGER) + 1) || '周'",
    ),
    "month": (
        "strftime('%Y-%m', e.created_at)",
        "strftime('%Y-%m', e.created_at) || '月'",
    ),
    "half_year": (
        "strftime('%Y', e.created_at) || CASE WHEN CAST(strftime('%m', e.created_at) AS INTEGER) <= 6 THEN 'H1' ELSE 'H2' END",
        "strftime('%Y', e.created_at) || CASE WHEN CAST(strftime('%m', e.created_at) AS INTEGER) <= 6 THEN '上半年' ELSE '下半年' END",
    ),
}


def trend_matrix(by: str = "week",
                 class_name: Optional[str] = None,
                 batch_nos: Optional[List[str]] = None) -> Dict[str, Any]:
    """错因演变曲线：{series: [{category_id, points: [{x, count}]}], x_labels: []}

    x 为录入错题的时间分桶（week 周 / month 月 / half_year 半年），
    纵轴为该错因人次（教师确认口径）；batch_nos 可选按批次过滤。
    """
    key_sql, label_sql = _BUCKET_SQL[by]
    where = ["e.teacher_action IS NOT NULL AND e.teacher_action != 'ignore'"]
    args: List[Any] = []
    if class_name:
        where.append("e.class_name = ?")
        args.append(class_name)
    if batch_nos:
        where.append(_batch_in_sql(batch_nos))
        args.extend(batch_nos)
    sql = (
        "SELECT {key} AS xk, {label} AS x, {fc} AS final_cat, COUNT(*) AS count "
        "FROM errors e WHERE {where} GROUP BY xk, x, final_cat ORDER BY xk, final_cat"
    ).format(key=key_sql, label=label_sql, fc=FINAL_CATEGORY_SQL,
             where=" AND ".join(where))
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()

    x_labels: List[str] = []
    seen_x = set()
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        x = r["x"]
        if not x:
            continue
        if x not in seen_x:
            seen_x.add(x)
            x_labels.append(x)
        by_cat.setdefault(r["final_cat"], []).append({"x": x, "count": r["count"]})
    # x 已按 xk 升序排列，直接透传
    series = [
        {"category_id": cat, "points": pts}
        for cat, pts in sorted(by_cat.items())
    ]
    return {"by": by, "class_name": class_name, "x_labels": x_labels, "series": series}


def student_category_counts(unit: Optional[str] = None,
                            class_name: Optional[str] = None,
                            exam_type: Optional[str] = None,
                            batch_nos: Optional[List[str]] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> List[Dict[str, Any]]:
    """(student_code, final_cat) -> 人次。决策单"个别辅导名单"的数据源。"""
    where: List[str] = []
    args: List[Any] = []
    _confirmed_filter(where, args, unit, class_name, exam_type,
                      batch_nos=batch_nos, date_from=date_from, date_to=date_to)
    sql = (
        "SELECT e.student_code AS code, {fc} AS final_cat, COUNT(*) AS count "
        "FROM errors e WHERE {where} GROUP BY e.student_code, final_cat"
    ).format(fc=FINAL_CATEGORY_SQL, where=" AND ".join(where))
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [dict(r) for r in rows]


def total_confirmed(unit: Optional[str] = None,
                    class_name: Optional[str] = None,
                    exam_type: Optional[str] = None,
                    batch_nos: Optional[List[str]] = None,
                    date_from: Optional[str] = None,
                    date_to: Optional[str] = None) -> int:
    where: List[str] = []
    args: List[Any] = []
    _confirmed_filter(where, args, unit, class_name, exam_type,
                      batch_nos=batch_nos, date_from=date_from, date_to=date_to)
    sql = "SELECT COUNT(*) AS c FROM errors e WHERE {}".format(" AND ".join(where))
    with get_conn() as conn:
        return conn.execute(sql, tuple(args)).fetchone()["c"]


def student_category_history(student_code: str, category_id: str) -> int:
    """该生该错因历史出现次数（教师确认口径）——E 类判别输入。"""
    sql = (
        "SELECT COUNT(*) AS c FROM errors e "
        "WHERE e.student_code=? AND {fc}=? "
        "AND e.teacher_action IS NOT NULL AND e.teacher_action != 'ignore'"
    ).format(fc=FINAL_CATEGORY_SQL)
    with get_conn() as conn:
        return conn.execute(sql, (student_code, category_id)).fetchone()["c"]


def student_profile(student_code: str, limit: int = 20) -> Optional[Dict[str, Any]]:
    """单个学生的错因画像（教师个体帮扶视图，仅本机查看）。

    返回：基本信息（含姓名，仅教师本机可见）+ 按类别计数（确认口径）
    + 月度趋势（每月错题数与五大错因组分布）+ 最近错题（题型差异化呈现
    所需的完整字段）。
    伦理边界：只提供原始记录与计数，不生成任何评价性结论、不参与排名。
    """
    with get_conn() as conn:
        stu = conn.execute(
            "SELECT student_code, name_local, class_name FROM students "
            "WHERE student_code=?", (student_code,)).fetchone()
        if not stu:
            return None
        cats = conn.execute(
            "SELECT {fc} AS category_id, COUNT(*) AS count FROM errors e "
            "WHERE e.student_code=? "
            "AND e.teacher_action IS NOT NULL AND e.teacher_action != 'ignore' "
            "GROUP BY category_id ORDER BY count DESC".format(fc=FINAL_CATEGORY_SQL),
            (student_code,)).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE student_code=? "
            "AND teacher_action IS NULL", (student_code,)).fetchone()["c"]
        rows = conn.execute(
            "SELECT {cols} FROM errors WHERE student_code=? "
            "ORDER BY created_at DESC LIMIT ?".format(cols=_ERROR_COLS),
            (student_code, limit)).fetchall()
        trend = conn.execute(
            "SELECT substr(e.created_at,1,7) AS ym, COUNT(*) AS total, "
            "SUM(CASE WHEN {fc} LIKE 'A%' THEN 1 ELSE 0 END) AS gA, "
            "SUM(CASE WHEN {fc} LIKE 'B%' THEN 1 ELSE 0 END) AS gB, "
            "SUM(CASE WHEN {fc} LIKE 'C%' THEN 1 ELSE 0 END) AS gC, "
            "SUM(CASE WHEN {fc} LIKE 'D%' THEN 1 ELSE 0 END) AS gD, "
            "SUM(CASE WHEN {fc} LIKE 'E%' THEN 1 ELSE 0 END) AS gE "
            "FROM errors e WHERE e.student_code=? "
            "AND e.teacher_action IS NOT NULL AND e.teacher_action != 'ignore' "
            "GROUP BY ym ORDER BY ym".format(fc=FINAL_CATEGORY_SQL),
            (student_code,)).fetchall()
    return {
        "student": dict(stu),
        "total_confirmed": sum(r["count"] for r in cats),
        "pending": pending,
        "categories": [dict(r) for r in cats],
        "trend": [dict(r) for r in trend],
        "recent": [_row_to_error(r) for r in rows],
    }


def override_stats(class_name: Optional[str] = None) -> Dict[str, Any]:
    """教师修正率（护AI 核心数据）：改判占比 + 改判分布 + AI vs 教师差异对。"""
    with get_conn() as conn:
        def _count(action: Optional[str]) -> int:
            sql = "SELECT COUNT(*) AS c FROM errors WHERE teacher_action IS NOT NULL"
            args: List[Any] = []
            if class_name:
                sql += " AND class_name=?"
                args.append(class_name)
            if action:
                sql += " AND teacher_action=?"
                args.append(action)
            return conn.execute(sql, tuple(args)).fetchone()["c"]

        confirmed = _count(None) - _count("ignore")
        modified = _count("modify")
        accepted = _count("accept")
        ignored = _count("ignore")

        dist_sql = ("SELECT category_id AS ai_cat, teacher_override AS teacher_cat, "
                    "COUNT(*) AS count FROM errors WHERE teacher_action='modify'")
        dist_args: List[Any] = []
        if class_name:
            dist_sql += " AND class_name=?"
            dist_args.append(class_name)
        dist_sql += " GROUP BY ai_cat, teacher_cat ORDER BY count DESC"
        dist_rows = conn.execute(dist_sql, tuple(dist_args)).fetchall()
    return {
        "confirmed": confirmed,
        "accepted": accepted,
        "modified": modified,
        "ignored": ignored,
        "override_rate": round(modified / confirmed, 4) if confirmed else 0.0,
        "distribution": [dict(r) for r in dist_rows],
    }


def scope_counts(unit: Optional[str] = None,
                 class_name: Optional[str] = None,
                 exam_type: Optional[str] = None,
                 batch_nos: Optional[List[str]] = None) -> Dict[str, int]:
    """看板三项计数（全部录入口径）：总数 / 待确认 / 涉及学生（代号数）。

    unit / class_name / exam_type / batch_nos 均参与过滤——选定后统计卡
    不得再显示全库数字；与 total_confirmed（教师确认口径）配合构成看板完整口径。
    """
    where: List[str] = []
    args: List[Any] = []
    if unit:
        where.append("unit=?")
        args.append(unit)
    if class_name:
        where.append("class_name=?")
        args.append(class_name)
    if exam_type:
        where.append("exam_type=?")
        args.append(exam_type)
    if batch_nos:
        where.append("COALESCE(NULLIF(batch_no, ''), unit) IN (%s)"
                     % ",".join("?" * len(batch_nos)))
        args.extend(batch_nos)
    scope = (" WHERE " + " AND ".join(where)) if where else ""

    def _count(extra: str) -> int:
        with get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM errors" + scope + extra,
                tuple(args)).fetchone()["c"]

    with get_conn() as conn:
        students = conn.execute(
            "SELECT COUNT(DISTINCT student_code) AS c FROM errors" + scope
            + (" AND " if where else " WHERE ") + "student_code != ''",
            tuple(args)).fetchone()["c"]
    return {
        "total": _count(""),
        "pending": _count(" AND teacher_action IS NULL" if where
                           else " WHERE teacher_action IS NULL"),
        "students": students,
    }


def weekly_trend(class_name: Optional[str] = None,
                 exam_type: Optional[str] = None,
                 batch_nos: Optional[List[str]] = None,
                 limit: int = 24) -> List[Dict[str, Any]]:
    """按录入错题时间的自然周分桶（全部录入，含待确认）——看板趋势用。

    week 为展示标签（如 2026-第37周），按自然周升序。
    """
    sql = (
        "SELECT strftime('%Y', created_at) || '-第' || "
        "(CAST(strftime('%W', created_at) AS INTEGER) + 1) || '周' AS week, "
        "COUNT(*) AS count FROM errors WHERE created_at IS NOT NULL"
    )
    args: List[Any] = []
    if class_name:
        sql += " AND class_name=?"
        args.append(class_name)
    if exam_type:
        sql += " AND exam_type=?"
        args.append(exam_type)
    if batch_nos:
        sql += " AND COALESCE(NULLIF(batch_no, ''), unit) IN (%s)" \
            % ",".join("?" * len(batch_nos))
        args.extend(batch_nos)
    sql += " GROUP BY week ORDER BY MIN(created_at)"
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [dict(r) for r in rows][:limit]


def class_baseline(question_key: str, class_name: Optional[str] = None) -> Optional[float]:
    """班级基准（E 类判别用）：同一题干的班级正确率。

    数据库中只有错题（正确题不录入），无法直接算正确率；
    采用可得的近似口径：该题同题干错题人次 / 班级人数。
    错题人次越少说明该题越"容易"（正确率越高）。
    """
    with get_conn() as conn:
        if class_name:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM errors WHERE question = ? AND class_name=?",
                (question_key, class_name),
            ).fetchone()
            size = conn.execute(
                "SELECT COUNT(*) AS c FROM students WHERE class_name=?", (class_name,)
            ).fetchone()["c"]
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM errors WHERE question = ?", (question_key,)
            ).fetchone()
            size = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
    wrong = row["c"]
    if size <= 0:
        return None
    rate = 1.0 - (wrong / size)
    return max(0.0, min(1.0, rate))
