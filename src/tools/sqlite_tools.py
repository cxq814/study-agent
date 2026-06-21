"""
SQLite 数据操作工具 — 封装 sqlite_client.py，加参数校验和异常捕获。

调用方：所有业务 Agent。
"""

import json
import logging
from typing import Optional, List, Dict, Any

from src.storage.sqlite_client import (
    get_user, upsert_user, get_course, list_courses, list_all_courses,
    get_timetable, add_timetable_entry, delete_timetable_entry,
    clear_user_minor_data, get_study_progress, add_study_progress,
    add_conflict_record, get_conflict_history, add_report, get_reports,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  参数校验辅助
# ══════════════════════════════════════════════════════

def _require_str(value: Any, name: str) -> Optional[str]:
    if not value or not isinstance(value, str):
        logger.warning("%s is required and must be str, got %s", name, type(value).__name__)
        return None
    return value


# ══════════════════════════════════════════════════════
#  用户操作
# ══════════════════════════════════════════════════════

def tool_get_user(user_id: str) -> Optional[dict]:
    """获取用户档案。失败返回 None。"""
    if not _require_str(user_id, "user_id"):
        return None
    try:
        return get_user(user_id)
    except Exception as e:
        logger.error("tool_get_user(%s) failed: %s", user_id, e)
        return None


def tool_upsert_user(user_id: str, **fields) -> bool:
    """更新或插入用户信息。返回 True/False。"""
    if not _require_str(user_id, "user_id"):
        return False
    try:
        upsert_user(user_id=user_id, **fields)
        return True
    except Exception as e:
        logger.error("tool_upsert_user(%s) failed: %s", user_id, e)
        return False


def tool_update_interests(user_id: str, interests: list) -> bool:
    """更新用户兴趣标签。"""
    if not _require_str(user_id, "user_id"):
        return False
    if not isinstance(interests, list):
        logger.warning("interests must be a list")
        return False
    try:
        interest_json = json.dumps(interests, ensure_ascii=False)
        upsert_user(user_id=user_id, interests=interest_json)
        return True
    except Exception as e:
        logger.error("tool_update_interests(%s) failed: %s", user_id, e)
        return False


# ══════════════════════════════════════════════════════
#  课程操作
# ══════════════════════════════════════════════════════

def tool_get_course(course_code: str) -> Optional[dict]:
    """获取课程信息。失败返回 None。"""
    if not _require_str(course_code, "course_code"):
        return None
    try:
        return get_course(course_code)
    except Exception as e:
        logger.error("tool_get_course(%s) failed: %s", course_code, e)
        return None


def tool_list_courses(program: str = None) -> List[Dict]:
    """列出课程，可选按专业过滤。失败返回空列表。"""
    try:
        return list_courses(program=program)
    except Exception as e:
        logger.error("tool_list_courses(program=%s) failed: %s", program, e)
        return []


# ══════════════════════════════════════════════════════
#  课表操作
# ══════════════════════════════════════════════════════

def tool_get_timetable(user_id: str, course_type: str = None) -> List[Dict]:
    """获取用户课表，可选按 major/minor 过滤。失败返回空列表。"""
    if not _require_str(user_id, "user_id"):
        return []
    try:
        return get_timetable(user_id, course_type=course_type)
    except Exception as e:
        logger.error("tool_get_timetable(%s) failed: %s", user_id, e)
        return []


def tool_add_timetable_entry(user_id: str, course_code: str,
                              course_type: str, **schedule) -> bool:
    """
    添加课表条目。

    必填: user_id, course_code, course_type
    可选: week_start, week_end, day_of_week, period_start, period_end,
          location, exam_time
    """
    if not _require_str(user_id, "user_id"):
        return False
    if not _require_str(course_code, "course_code"):
        return False
    if course_type not in ("major", "minor"):
        logger.warning("course_type must be 'major' or 'minor', got %s", course_type)
        return False
    try:
        entry = {
            "user_id": user_id,
            "course_code": course_code,
            "course_type": course_type,
            "week_start": schedule.pop("week_start", 1),
            "week_end": schedule.pop("week_end", 16),
            "day_of_week": schedule.pop("day_of_week", 1),
            "period_start": schedule.pop("period_start", 3),
            "period_end": schedule.pop("period_end", 5),
            "location": schedule.pop("location", ""),
            "exam_time": schedule.pop("exam_time", ""),
        }
        add_timetable_entry(entry)
        return True
    except Exception as e:
        logger.error("tool_add_timetable_entry(%s, %s) failed: %s", user_id, course_code, e)
        return False


def tool_delete_timetable_entry(entry_id: int) -> bool:
    """删除课表条目。"""
    if not isinstance(entry_id, int) or entry_id <= 0:
        logger.warning("entry_id must be positive int, got %s", entry_id)
        return False
    try:
        delete_timetable_entry(entry_id)
        return True
    except Exception as e:
        logger.error("tool_delete_timetable_entry(%s) failed: %s", entry_id, e)
        return False


def tool_clear_minor_courses(user_id: str) -> bool:
    """清空用户辅修课表并重置辅修状态。"""
    if not _require_str(user_id, "user_id"):
        return False
    try:
        clear_user_minor_data(user_id)
        return True
    except Exception as e:
        logger.error("tool_clear_minor_courses(%s) failed: %s", user_id, e)
        return False


# ══════════════════════════════════════════════════════
#  进度操作
# ══════════════════════════════════════════════════════

def tool_get_progress(user_id: str, course_code: str = None) -> List[Dict]:
    """获取学习进度记录。失败返回空列表。"""
    if not _require_str(user_id, "user_id"):
        return []
    try:
        return get_study_progress(user_id, course_code=course_code)
    except Exception as e:
        logger.error("tool_get_progress(%s) failed: %s", user_id, e)
        return []


def tool_add_progress(user_id: str, course_code: str, week: int,
                       homework: float = None, attendance: str = None,
                       quiz: float = None, note: str = None) -> bool:
    """添加一条学习进度记录。"""
    if not _require_str(user_id, "user_id"):
        return False
    if not _require_str(course_code, "course_code"):
        return False
    if not isinstance(week, int) or week < 1:
        logger.warning("week must be positive int, got %s", week)
        return False
    try:
        entry = {
            "user_id": user_id,
            "course_code": course_code,
            "week": week,
        }
        if homework is not None:
            entry["homework_score"] = float(homework)
        if attendance is not None:
            entry["attendance"] = str(attendance)
        if quiz is not None:
            entry["quiz_score"] = float(quiz)
        if note is not None:
            entry["note"] = str(note)
        add_study_progress(entry)
        return True
    except Exception as e:
        logger.error("tool_add_progress(%s, wk%s) failed: %s", course_code, week, e)
        return False


# ══════════════════════════════════════════════════════
#  冲突 & 报告
# ══════════════════════════════════════════════════════

def tool_add_conflict_record(user_id: str, conflict_data: dict) -> bool:
    """写入冲突记录。"""
    if not _require_str(user_id, "user_id"):
        return False
    if not isinstance(conflict_data, dict):
        return False
    try:
        conflict_data["user_id"] = user_id
        add_conflict_record(conflict_data)
        return True
    except Exception as e:
        logger.error("tool_add_conflict_record(%s) failed: %s", user_id, e)
        return False


def tool_get_conflict_history(user_id: str) -> List[Dict]:
    """获取冲突历史。"""
    if not _require_str(user_id, "user_id"):
        return []
    try:
        return get_conflict_history(user_id)
    except Exception as e:
        logger.error("tool_get_conflict_history(%s) failed: %s", user_id, e)
        return []


def tool_add_report(user_id: str, report_type: str,
                     content_md: str, summary_json: str = None) -> bool:
    """持久化报告。"""
    if not _require_str(user_id, "user_id"):
        return False
    if not content_md:
        return False
    try:
        add_report({
            "user_id": user_id,
            "report_type": report_type,
            "content_md": content_md,
            "summary_json": summary_json or "{}",
        })
        return True
    except Exception as e:
        logger.error("tool_add_report(%s) failed: %s", user_id, e)
        return False


def tool_get_reports(user_id: str, report_type: str = None) -> List[Dict]:
    """获取历史报告。"""
    if not _require_str(user_id, "user_id"):
        return []
    try:
        return get_reports(user_id, report_type=report_type)
    except Exception as e:
        logger.error("tool_get_reports(%s) failed: %s", user_id, e)
        return []
