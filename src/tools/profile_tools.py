"""
用户个人资料工具 — 验证、格式化、默认值。
供侧边栏表单和 data_manager agent 共用。
"""

from typing import Dict, Optional

# 可选的主修专业列表
MAJOR_OPTIONS = [
    "计算机科学与技术", "软件工程", "数据科学与大数据技术",
    "人工智能", "电子信息工程", "通信工程",
    "金融学", "会计学", "经济学", "工商管理",
    "法学", "英语", "数学与应用数学", "统计学",
    "其他",
]

# 年级选项
GRADE_OPTIONS = ["2024级", "2023级", "2022级", "2021级", "2020级", "研究生"]


def validate_profile(student_name: str, major: str) -> tuple[bool, Optional[str]]:
    """验证必填字段。返回 (ok, error_message)。"""
    if not student_name or not student_name.strip():
        return False, "姓名不能为空"
    if len(student_name.strip()) > 50:
        return False, "姓名过长（最多50字符）"
    if not major or not major.strip():
        return False, "主修专业不能为空"
    return True, None


def build_user_updates(student_name: str, major: str,
                       grade: str = None,
                       interests_str: str = None,
                       existing: Dict = None) -> Dict:
    """
    构建传给 upsert_user 的参数字典。
    interests_str: 逗号分隔的兴趣文本，如 "金融,数据科学"
    """
    existing = existing or {}
    interests = None
    if interests_str and interests_str.strip():
        import json
        parts = [s.strip() for s in interests_str.replace("，", ",").split(",") if s.strip()]
        interests = json.dumps(parts, ensure_ascii=False)

    return {
        "user_id": existing.get("user_id", ""),
        "student_name": student_name.strip(),
        "major": major.strip(),
        "grade": grade or existing.get("grade"),
        "available_slots": existing.get("available_slots"),
        "interests": interests or existing.get("interests"),
        "has_minor": existing.get("has_minor", 0),
        "minor_program": existing.get("minor_program"),
    }
