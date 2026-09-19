"""学生名册匹配：拍照批改解析（photo_check）共用的姓名 → 学生工具。

匹配结果仅供老师确认，可手动改选——最终以老师确认的学号为准；
反标签化：姓名只用于本机名册匹配，不写入任何错题记录或评价。
"""
from typing import Any, Dict

from ..data import db


def match_roster(name: str, class_name: str = "") -> Dict[str, Any]:
    """姓名 → 名册匹配：先精确、再互相包含，否则未匹配。

    公共工具：拍照批改解析（photo_check）在读出学生姓名后调用；
    class_name 限定匹配范围（批改页右上角所选班级），空 = 全部班级。
    """
    empty = {"matched": False, "student_code": "", "name_local": ""}
    if not name:
        return empty
    roster = db.list_students(class_name or "")
    for s in roster:
        if (s.get("name_local") or "").strip() == name:
            return {"matched": True, "student_code": s["student_code"],
                    "name_local": s["name_local"]}
    for s in roster:
        nl = (s.get("name_local") or "").strip()
        if nl and (name in nl or nl in name):
            return {"matched": True, "student_code": s["student_code"],
                    "name_local": s["name_local"]}
    return empty
