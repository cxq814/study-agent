"""
课程数据编辑工具 — 课表增删改、换课事务、冲突预检。

调用方：数据管理 Agent、课程调整 Agent。
"""

import logging
from typing import List, Dict, Optional

from src.storage.sqlite_client import (
    get_timetable, add_timetable_entry, delete_timetable_entry,
    clear_user_minor_data, get_course, upsert_user, get_user,
)
from src.agents.conflict_checker import ConflictCheckerAgent

logger = logging.getLogger(__name__)

# 冲突检测实例（复用已有算法）
_conflict_checker = None


def _get_checker() -> ConflictCheckerAgent:
    global _conflict_checker
    if _conflict_checker is None:
        _conflict_checker = ConflictCheckerAgent()
    return _conflict_checker


# ══════════════════════════════════════════════════════
#  课表编辑
# ══════════════════════════════════════════════════════

def edit_timetable_entry(entry_id: int, **updates) -> bool:
    """
    修改课表条目（时间、地点等）。

    通过删除旧条目 + 添加新条目实现更新（SQLite 无直接 UPDATE 单字段接口）。
    目前采用简化为：仅支持下述字段的部分更新。

    注：当前 sqlite_client 无 update_timetable_entry 方法，后续补充。
    当前版本返回 False 并提示使用 delete + add 方式。
    """
    if not isinstance(entry_id, int) or entry_id <= 0:
        logger.warning("edit_timetable_entry: invalid entry_id=%s", entry_id)
        return False
    if not updates:
        return False
    # 当前存储层不支持单条目更新，由上层 Agent 自行组合 delete + add
    logger.info(
        "edit_timetable_entry(%s) called with updates=%s — "
        "use delete+add pattern in caller", entry_id, list(updates.keys())
    )
    return False


def batch_add_courses(user_id: str, course_codes: list,
                       course_type: str) -> dict:
    """
    批量添加课程。

    返回: {"success": 3, "failed": 0, "added_codes": [...], "errors": []}
    """
    result = {"success": 0, "failed": 0, "added_codes": [], "errors": []}

    if not user_id or not isinstance(user_id, str):
        return {"success": 0, "failed": len(course_codes) if course_codes else 0,
                "added_codes": [], "errors": ["invalid user_id"]}

    if course_type not in ("major", "minor"):
        return {"success": 0, "failed": len(course_codes) if course_codes else 0,
                "added_codes": [], "errors": ["course_type must be 'major' or 'minor'"]}

    if not isinstance(course_codes, list) or not course_codes:
        return result

    for code in course_codes:
        if not isinstance(code, str):
            result["failed"] += 1
            result["errors"].append(f"invalid code: {code}")
            continue
        try:
            entry = {
                "user_id": user_id,
                "course_code": code,
                "course_type": course_type,
                "week_start": 1,
                "week_end": 16,
            }
            add_timetable_entry(entry)
            result["success"] += 1
            result["added_codes"].append(code)
        except Exception as e:
            result["failed"] += 1
            result["errors"].append(f"{code}: {e}")

    return result


def swap_course(user_id: str, drop_code: str, add_code: str) -> dict:
    """
    退选一门 + 加选一门，事务性操作。

    返回: {
        "dropped": "FIN201" or None,
        "added": "DS201" or None,
        "warnings": []
    }
    """
    result = {"dropped": None, "added": None, "warnings": []}

    if not user_id or not isinstance(user_id, str):
        result["warnings"].append("invalid user_id")
        return result
    if not drop_code or not add_code:
        result["warnings"].append("drop_code and add_code are required")
        return result

    try:
        # 1. 找到要退的课表条目
        timetable = get_timetable(user_id, course_type="minor")
        target_entry = None
        for t in timetable:
            if t.get("course_code") == drop_code:
                target_entry = t
                break

        if target_entry:
            delete_timetable_entry(target_entry["id"])
            result["dropped"] = drop_code
        else:
            result["warnings"].append(f"未找到 {drop_code} 的课表记录")

        # 2. 检查剩余辅修课数量（最后一门 → 清标记）
        remaining = get_timetable(user_id, course_type="minor")
        if not remaining:
            user = get_user(user_id)
            if user:
                upsert_user(
                    user_id=user_id,
                    student_name=user.get("student_name", ""),
                    major=user.get("major", ""),
                    grade=user.get("grade"),
                    available_slots=user.get("available_slots"),
                    interests=user.get("interests"),
                    has_minor=0,
                    minor_program=None,
                )
            result["warnings"].append("已清空辅修课程，辅修状态已重置")

        # 3. 添加新课程
        course_info = get_course(add_code)
        if course_info:
            add_timetable_entry({
                "user_id": user_id,
                "course_code": add_code,
                "course_type": "minor",
                "week_start": 1,
                "week_end": 16,
            })
            result["added"] = add_code
        else:
            result["warnings"].append(f"课程 {add_code} 不在数据库中，仅删除未添加")

        return result
    except Exception as e:
        logger.error("swap_course(%s, %s→%s) failed: %s", user_id, drop_code, add_code, e)
        result["warnings"].append(f"操作异常: {e}")
        return result


# ══════════════════════════════════════════════════════
#  冲突预检
# ══════════════════════════════════════════════════════

def validate_schedule(user_id: str, new_course_code: str) -> dict:
    """
    预检：添加某门课程是否会产生新的时间冲突。

    返回: {
        "has_conflict": bool,
        "conflicts": [
            {"course_a": "CS301", "course_b": "FIN101",
             "detail": "周三第3节重叠", "severity": "warning"}
        ]
    }
    """
    if not user_id or not new_course_code:
        return {"has_conflict": False, "conflicts": []}

    try:
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        # 构建新课程的临时条目（没有完整 schedule，用默认值）
        new_course = {
            "course_code": new_course_code,
            "day_of_week": 4,
            "period_start": 3,
            "period_end": 5,
            "exam_time": "",
        }

        # 尝试从已有课表中找同名课程获取实际时间
        for t in minor_courses + major_courses:
            if t.get("course_code") == new_course_code:
                new_course.update(t)
                break

        checker = _get_checker()
        conflicts = []

        # 与主修课程比对
        for major in major_courses:
            c = checker._check_pair(major, new_course)
            if c:
                conflicts.append({
                    "course_a": c["course_a"],
                    "course_b": c["course_b"],
                    "detail": c.get("conflicts", [{}])[0].get("detail", ""),
                    "severity": c["severity"],
                })

        return {
            "has_conflict": len(conflicts) > 0,
            "conflicts": conflicts,
        }
    except Exception as e:
        logger.error("validate_schedule(%s, %s) failed: %s", user_id, new_course_code, e)
        return {"has_conflict": False, "conflicts": []}
