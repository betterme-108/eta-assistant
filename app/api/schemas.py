"""Pydantic 请求模型（集中定义，路由层共用）。"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ScanRequest(BaseModel):
    question: Optional[str] = ""
    answer: Optional[str] = ""
    correct: Optional[str] = ""
    qtype: Optional[str] = ""
    passage: Optional[str] = ""  # 阅读理解/完形填空的完整原文（题型差异化呈现用）
    options: Optional[List[str]] = None
    unit: Optional[str] = None
    week: Optional[int] = None
    exam_type: Optional[str] = None   # 任务类型：课时作业 / 考试试卷
    batch_no: Optional[str] = None    # 唯一标识（手动创建/编辑，或从已有标识选择）
    class_name: Optional[str] = None
    student_code: Optional[str] = None
    image_base64: Optional[str] = None


class BatchScanRequest(BaseModel):
    """批量粘贴录入：每行一题，同题只归因一次、按学号展开成多条记录。

    行格式（| 或 Tab 分隔，支持从 Excel 直接粘贴）：
      学号[,学号…] | 题干 | 学生作答 | 正确答案(可省)
      题干 | 学生作答 | 正确答案(可省)   ← 不关联学生
    """
    text: str
    qtype: Optional[str] = ""
    passage: Optional[str] = ""  # 阅读理解/完形原文（整批共用，如一篇文章的多道错题）
    unit: Optional[str] = None
    week: Optional[int] = None
    exam_type: Optional[str] = None
    batch_no: Optional[str] = None
    class_name: Optional[str] = None


class PhotoSplitRequest(BaseModel):
    """拍照切题第一步：多图 → 逐页 OCR → AI 切题（仅预览，不入库）。

    images_base64：1–10 张（多页试卷可连拍多张，跨页题自动合并）。
    """
    images_base64: List[str]


class PhotoQuestionIn(BaseModel):
    """切题结果中教师勾选/编辑后的一题。"""
    qtype: Optional[str] = ""
    question: str
    options: Optional[List[str]] = None
    answer: Optional[str] = ""       # 学生作答（拍照场景常缺省，可空）
    correct: Optional[str] = ""
    passage: Optional[str] = ""
    students: Optional[List[str]] = None  # 关联学生代号（可空 → 班级级记录）


class PhotoSubmitRequest(BaseModel):
    """拍照切题第二步：提交勾选的题目 → 逐题归因 → 待确认入库。"""
    questions: List[PhotoQuestionIn]
    unit: Optional[str] = None
    week: Optional[int] = None
    exam_type: Optional[str] = None
    batch_no: Optional[str] = None
    class_name: Optional[str] = None


class BulkConfirmRequest(BaseModel):
    """归因复核批量确认（减负）：待复核记录一键采纳/忽略。

    min_confidence 只在 action=accept 时生效（如 0.8 = 仅采纳高置信度）；
    batch_no 可选：只作用于某次作业/考试的待确认记录。
    """
    action: str = Field(..., description="accept / ignore")
    min_confidence: Optional[float] = None
    class_name: Optional[str] = None
    batch_no: Optional[str] = None


class ConfirmRequest(BaseModel):
    action: str = Field(..., description="accept / modify / ignore")
    override: Optional[str] = None


class StudentImportRequest(BaseModel):
    rows: Optional[List[Dict[str, Any]]] = None
    text: Optional[str] = None  # 每行 "代号,姓名,班级" 或 "代号,姓名"


class StudentCreateRequest(BaseModel):
    student_code: str
    name_local: Optional[str] = None
    class_name: Optional[str] = None


class StudentUpdateRequest(BaseModel):
    new_code: Optional[str] = None       # 改代号（级联更新错题关联）
    name_local: Optional[str] = None     # None = 不改
    class_name: Optional[str] = None     # None = 不改


class ClassCreateRequest(BaseModel):
    name: str


class ClassRenameRequest(BaseModel):
    new_name: str


class PracticeGenRequest(BaseModel):
    """生成练习：单类（category_id，兼容旧客户端）或多类（category_ids，一次多套）。

    date_from/date_to 圈定错题来源阶段（留空 = 全部历史）：练习生成时把范围内的
    真实错例注入提示词，更贴合班级实际，并随练习留档 scope。
    """
    category_id: Optional[str] = None
    category_ids: Optional[List[str]] = None
    n: int = Field(3, ge=1, le=100)  # 每套题量 1–100，超过 10 题后端自动分批生成
    class_name: Optional[str] = None
    date_from: Optional[str] = None   # 阶段起点（舍），YYYY-MM-DD
    date_to: Optional[str] = None     # 阶段止点（含当天）

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _empty_date(cls, v):
        return (v or "").strip() or None


class PaperGenRequest(BaseModel):
    """AI 组卷：基于班级/任务类型的已确认错题分布生成综合试卷。

    difficulty / qtype_pref 留空 = 自动适配（与班级错题对应的初中常考难度、自动混合题型）；
    date_from/date_to 圈定错题来源阶段（留空 = 全部历史）。
    """
    title: Optional[str] = None
    n: int = Field(10, ge=1, le=50)
    unit: Optional[str] = None          # 旧参数兼容保留
    exam_type: Optional[str] = None     # 任务类型：课时作业 / 考试试卷
    class_name: Optional[str] = None
    difficulty: Optional[Literal["easy", "mid", "hard"]] = None
    qtype_pref: Optional[Literal["mc", "read", "write"]] = None
    date_from: Optional[str] = None    # 阶段起点（舍）
    date_to: Optional[str] = None      # 阶段止点（含当天）

    @field_validator("title", "unit", "exam_type", "class_name",
                     "difficulty", "qtype_pref", "date_from", "date_to", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        """前端未选时传空字符串 → 归为 None（避免 Literal 校验报 422）。"""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class AIStrategyRequest(BaseModel):
    """决策单 AI 讲评策略：基于规则决策单数据由 LLM 生成执行策略。"""
    unit: Optional[str] = None        # 旧参数兼容保留
    class_name: Optional[str] = None
    exam_type: Optional[str] = None   # 任务类型：课时作业 / 考试试卷
    batch_nos: Optional[List[str]] = None   # 批次多选（某几次作业/考试）
    date_from: Optional[str] = None   # 阶段范围（YYYY-MM-DD，录入时间口径）
    date_to: Optional[str] = None


# ---------------------------------------------------------------- 批改（听写 / 作业）

class DictationWordIn(BaseModel):
    """听写词表一项（no 从 1 开始）。"""
    no: int
    en: str
    zh: Optional[str] = ""


class DictationAnswerIn(BaseModel):
    """一名学生的作答：items 按词表顺序对齐（不足自动补空）。"""
    student_code: Optional[str] = ""
    items: List[str] = []


class DictationCheckRequest(BaseModel):
    words: List[DictationWordIn]
    answers: List[DictationAnswerIn]
    unit: Optional[str] = None
    week: Optional[int] = None
    class_name: Optional[str] = None
    exam_type: Optional[str] = None   # 课时作业 / 考试试卷
    batch_no: Optional[str] = None    # 唯一标识（手动创建/编辑）
    title: Optional[str] = None


class AssignmentKeyIn(BaseModel):
    """作业/试卷答案一项：subjective 不填时按题型自动判断（完成句子/概要补全/
    任务型阅读/书面表达为主观题；书面表达走分维评分）。
    options/passage 为拍照批改解析带出的选项与阅读/完形原文——判错的题随
    错题入库，供「错题确认」页完整展示与组卷复用。"""
    no: int
    question: Optional[str] = ""
    qtype: Optional[str] = ""
    answer: Optional[str] = ""
    score: Optional[float] = 1.0
    subjective: Optional[bool] = None
    options: Optional[List[str]] = None
    passage: Optional[str] = ""


class AssignmentAnswerIn(BaseModel):
    """一名学生的作答：items 可为 [{no, answer, file}] 或按题号顺序的字符串列表。

    name/files 为文件夹上传场景的登记信息：学生姓名（二级目录名）与
    该生本次上传的照片清单（相对批次根目录的「学生/文件名」）。
    """
    student_code: Optional[str] = ""
    name: Optional[str] = ""
    files: Optional[List[str]] = None
    items: List[Any] = []


class AssignmentCheckRequest(BaseModel):
    key: List[AssignmentKeyIn]
    answers: List[AssignmentAnswerIn]
    unit: Optional[str] = None
    week: Optional[int] = None
    class_name: Optional[str] = None
    exam_type: Optional[str] = None   # 批改类型：课时作业 / 考试试卷
    title: Optional[str] = None
    batch_no: Optional[str] = None    # 唯一标识（如 1单元2课时 / 半期考试），定位每一次提交
    folder: Optional[str] = None      # 批次文件夹全名（班级-批改类型-唯一标识），存入报告备查


class OrganizeUploadRequest(BaseModel):
    """上传预检：批次文件夹内全部照片的相对路径（含批次根目录名前缀）。

    只解析路径不读照片：返回学生分组与统一信息预填建议，选择文件夹后立即调用。
    exam_type：当前批改页的批改类型（课时作业 / 考试试卷）——两段式
    「班级-唯一标识」批次根命名时作为批改类型来源（页面即类型）。
    """
    paths: List[str]
    exam_type: Optional[str] = None


class PhotoParseItem(BaseModel):
    """一张上传照片：path 为含批次根目录的相对路径（批次根/学生姓名/文件名）。"""
    path: str
    image_base64: str


class PhotoParseRequest(BaseModel):
    """拍照批改解析：整份批次文件夹照片 → 按学生分组 AI 解析题目与作答。

    items：每个学生可多张照片（同一夹内按文件名自然排序即页序）。
    exam_type：当前批改页的批改类型（两段式批次根命名时使用，同上）。
    class_name：批改页右上角所选班级——名册匹配只在该班学生里找。
    """
    items: List[PhotoParseItem]
    exam_type: Optional[str] = None
    class_name: Optional[str] = None


class MdReparseRequest(BaseModel):
    """智能体提取原文修改后重新结构化：老师直接编辑 Markdown 原文（修正识别错误
    或补漏），提交后按批改类型重新解析出该生的题目/作答/参考答案。"""
    exam_type: Optional[str] = None
    markdown: str


class SessionUpdateRequest(BaseModel):
    """手动修改批改记录的统一信息（班级/单元/周次/批改类型/唯一标识/标题）。

    未传的字段不修改；week 传 0 表示清空。
    """
    title: Optional[str] = None
    class_name: Optional[str] = None
    unit: Optional[str] = None
    week: Optional[int] = None
    exam_type: Optional[str] = None
    batch_no: Optional[str] = None


class StudentCleanupRequest(BaseModel):
    """学生历史数据清理维度。

    dimension：1w（一周前）/ 1m（一个月前）/ all（全部清除）/
    category（按错因类别，需 category_id）/ qtype（按题型，需 qtype）。
    """
    dimension: str = "1m"
    category_id: Optional[str] = None
    qtype: Optional[str] = None
