"""上传组织：批次文件夹命名的唯一权威解析器。

命名规范（班级-批改类型-唯一标识 / 学生姓名 / 照片）：
  一级目录（批次文件夹）  班级-批改类型-唯一标识   例：九年级5班-考试试卷-半期考试
  二级目录（学生文件夹）  学生姓名                 例：林一诺
  三级（照片文件）        该生本次作业/试卷照片     多张按文件名自然排序（即页序）

两段式「班级-唯一标识」（例：9年级20班-1单元2课时）同样支持：批改类型
优先取当前批改页传入的值（课时作业批改 / 考试试卷批改是两个页面，
页面本身就是类型）；无页面信息时从唯一标识识别（含考/试卷/测 → 考试试卷，
考试特征优先；含单元/课时/作业/练习 → 课时作业），识别后以 warnings 提示；
第 2 段本身就是批改类型（缺唯一标识）或两种途径都认不出时报错请改名。

批改类型仅两值：课时作业 / 考试试卷（对应「课时作业批改」「考试试卷批改」两页）。
唯一标识（如 1单元2课时、第一次月考、半期考试）定位每一次提交，与班级、
批改类型、学生姓名、文件名合成完整定位链——每个人的每一次提交数据都可准确定位：
  班级 + 批改类型 + 唯一标识 → 一次提交（check_sessions.batch_no）
  学生姓名                    → 一个人（paper_files.student_name）
  学生/文件名                 → 一张照片（paper_files.file_name）
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# 批改类型全集（三段式命名第 2 段的规范值）
CLASS_TYPES = ("课时作业", "考试试卷")

# 一次上传的规模上限（超出给直白提示，避免误传整盘照片）
MAX_STUDENTS = 80          # 单批学生数
MAX_FILES_PER_STUDENT = 12  # 每生照片数
MAX_FILES_TOTAL = 200       # 单批照片总数

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic"}
_UNIT_RE = re.compile(r"(\d+)\s*单元")

_NAMING_SPEC = ("「班级-批改类型-唯一标识」（如 九年级5班-考试试卷-半期考试、"
                "九年级5班-课时作业-1单元2课时）；也支持两段式「班级-唯一标识」"
                "（如 9年级20班-1单元2课时），批改类型自动识别")

# 两段式命名第 2 段（唯一标识）里的批改类型特征（考试特征优先：「单元测试」属考试）
_EXAM_TAG_RE = re.compile(r"考|试卷|测")
_LESSON_TAG_RE = re.compile(r"单元|课时|作业|练习")


def clean_path(p: str) -> str:
    """相对路径归一：反斜杠转正斜杠、去首尾空白与 ./ 前缀（前后端统一口径）。"""
    return (str(p or "")).replace("\\", "/").strip().lstrip("./")


def natural_key(name: str) -> List[Any]:
    """文件名自然排序键：a2 < a10（页序按文件名排序时数字按数值比较）。"""
    return [int(t) if t.isdigit() else t
            for t in re.split(r"(\d+)", (name or "").lower())]


def _normalize_type(seg: str) -> Optional[Tuple[str, bool]]:
    """批改类型段归一：精确两值直过；轻度容错（含「作业」→课时作业，
    含「考/试卷/测」→考试试卷）返回 (归一值, 是否精确)；无法识别返回 None。"""
    seg = (seg or "").strip()
    if seg in CLASS_TYPES:
        return seg, True
    if "作业" in seg:
        return "课时作业", False
    if "考" in seg or "试卷" in seg or "测" in seg:
        return "考试试卷", False
    return None


def _infer_exam_type(seg: str) -> Optional[str]:
    """两段式「班级-唯一标识」的第 2 段 → 推断批改类型。

    考试特征（考/试卷/测）优先；其余含单元/课时/作业/练习判课时作业；
    看不出是作业还是考试返回 None（由调用方报错请改名）。
    """
    seg = (seg or "").strip()
    if _EXAM_TAG_RE.search(seg):
        return "考试试卷"
    if _LESSON_TAG_RE.search(seg):
        return "课时作业"
    return None


def parse_batch_folder(name: str, exam_type: str = "") -> Dict[str, Any]:
    """批次文件夹名 → {folder, class_name, exam_type, batch_no, unit, title, warnings}。

    命名非法抛 ValueError（中文提示含改名建议），由路由层转 400。
    三段式：split("-", 2) → [班级, 批改类型, 唯一标识]，第 3 段可含连字符
    （如「1单元-2课时」整体作为唯一标识）；exam_type 参数不参与（文件夹权威）。
    两段式「班级-唯一标识」：批改类型取 exam_type（当前批改页传入，如
    「9年级20班-1单元2课时」→ 页面类型）；未传时从唯一标识推断（如
    「九年级5班-半期考试」→ 考试试卷）并附提示；都认不出时报错。
    """
    folder = (name or "").strip().strip("/")
    if not folder:
        raise ValueError("文件夹名为空：请按 %s 命名批次文件夹" % _NAMING_SPEC)
    parts = [p.strip() for p in folder.split("-", 2)]
    warnings: List[str] = []
    if len(parts) >= 3 and all(parts):
        class_name = parts[0]
        hit = _normalize_type(parts[1])
        if hit is None:
            raise ValueError(
                "文件夹「%s」第 2 段「%s」不是批改类型（课时作业 / 考试试卷），"
                "请按 %s 命名" % (folder, parts[1], _NAMING_SPEC))
        exam_type, exact = hit
        if not exact:
            warnings.append("批改类型「%s」已按「%s」处理；建议文件夹名直接写「%s」"
                            % (parts[1], exam_type, exam_type))
        batch_no = parts[2]
    elif len(parts) == 2 and parts[0] and parts[1]:
        class_name = parts[0]
        if parts[1] in CLASS_TYPES:   # 「班级-批改类型」：缺唯一标识，无法凭空补
            raise ValueError(
                "文件夹「%s」缺唯一标识：建议改为「%s-唯一标识」"
                "（如 %s-1单元2课时）" % (folder, folder, folder))
        page_type = (exam_type or "").strip()
        if page_type in CLASS_TYPES:   # 批改类型取当前批改页（页面即类型）
            exam_type = page_type
        else:                          # 无页面信息：从唯一标识推断保底
            exam_type = _infer_exam_type(parts[1])
            if exam_type is None:
                raise ValueError(
                    "文件夹「%s」看不出是课时作业还是考试试卷：%s。"
                    "请改名标明（如在班级后加「课时作业-」或「考试试卷-」）"
                    % (folder, _NAMING_SPEC))
            warnings.append("文件夹「%s」是两段式命名，已按「%s」归类（按名称识别）；"
                            "建议改为「%s-%s-%s」"
                            % (folder, exam_type, class_name, exam_type, parts[1]))
        batch_no = parts[1]
    else:
        raise ValueError(
            "文件夹「%s」不是 3 段式命名 %s；如果选中的是包含多个批次的上级文件夹，"
            "请进入具体的批次文件夹再上传" % (folder, _NAMING_SPEC))
    m = _UNIT_RE.search(batch_no)
    unit = "Unit %s" % m.group(1) if m else ""
    return {
        "folder": folder,
        "class_name": class_name,
        "exam_type": exam_type,
        "batch_no": batch_no,
        "unit": unit,
        "title": batch_no,
        "warnings": warnings,
    }


def organize(paths: List[str], exam_type: str = "") -> Dict[str, Any]:
    """批次文件夹内全部照片相对路径 → 规范分组与统一信息预填建议。

    结构约定：路径 = 批次根/学生姓名/照片文件；单一批次根、二级目录即学生、
    文件名自然排序即页序。exam_type 为当前批改页的批改类型（两段式批次根
    命名时作为批改类型来源）。结构小问题以 warnings 呈现（不阻断），
    硬错误（无照片 / 多批次根 / 根名不合规 / 无学生文件夹 / 超限）抛 ValueError。
    """
    cleaned = [clean_path(p) for p in (paths or [])]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        raise ValueError("没有照片：请选择按 %s 整理的批次文件夹" % _NAMING_SPEC)

    roots = {p.split("/", 1)[0] for p in cleaned}
    if len(roots) > 1:
        raise ValueError("一次只能上传一个批次的文件夹（检测到 %d 个：%s）——请分开上传"
                         % (len(roots), "、".join(sorted(roots)[:3])))
    root = roots.pop()
    meta = parse_batch_folder(root, exam_type)

    by_student: Dict[str, List[str]] = {}
    stray: List[str] = []
    for p in cleaned:
        rest = p[len(root) + 1:] if len(p) > len(root) + 1 else ""
        segs = [s for s in rest.split("/") if s]
        if not segs:
            continue
        ext = os.path.splitext(segs[-1])[1].lower()
        if ext not in _IMAGE_EXTS:
            continue
        if len(segs) < 2 or not segs[0].strip():
            stray.append(segs[-1])
            continue
        student = segs[0].strip()
        # 保留完整相对路径（以学生姓名开头，可含子目录）：与前端提交的路径一一对应
        by_student.setdefault(student, []).append("/".join(segs))

    warnings = list(meta["warnings"])
    if stray:
        warnings.append("%d 张照片不在任何学生文件夹下，已忽略（照片需放在学生姓名"
                        "子文件夹里）：%s" % (len(stray), "、".join(stray[:3])
                                             + ("等" if len(stray) > 3 else "")))
    if not by_student:
        raise ValueError("批次文件夹「%s」里没有学生子文件夹：二级目录应按学生姓名"
                         "一人一夹，夹内放该生照片" % root)
    if len(by_student) > MAX_STUDENTS:
        raise ValueError("一次最多 %d 名学生（检测到 %d 名）——请分批上传"
                         % (MAX_STUDENTS, len(by_student)))
    total = sum(len(v) for v in by_student.values())
    if total > MAX_FILES_TOTAL:
        raise ValueError("一次最多 %d 张照片（检测到 %d 张）——请分批上传"
                         % (MAX_FILES_TOTAL, total))

    groups: List[Dict[str, Any]] = []
    for student in sorted(by_student):
        files = sorted(by_student[student], key=natural_key)
        if len(files) > MAX_FILES_PER_STUDENT:
            warnings.append("学生「%s」有 %d 张照片，超过每人 %d 张上限，多余已忽略"
                            % (student, len(files), MAX_FILES_PER_STUDENT))
            files = files[:MAX_FILES_PER_STUDENT]
        if "-" in student and _normalize_type(student):
            warnings.append("文件夹「%s」看起来是批次名而不是学生姓名，请检查目录层级"
                            % student)
        groups.append({"name": student, "files": files})

    meta["warnings"] = warnings
    return {"meta": meta, "groups": groups, "warnings": warnings}
