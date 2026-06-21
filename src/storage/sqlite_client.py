"""
SQLite 连接管理与 CRUD 操作。

职责：
    1. 数据库初始化（建表 + 种子数据）
    2. 连接获取与关闭
    3. 基础 CRUD（增删改查）
"""

import sqlite3
import json
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from config.settings import SQLITE_PATH, SCHEMA_PATH, SEED_PATH


# ── 连接管理 ────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接（自动启用 WAL 模式 + 外键）。"""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """上下文管理器，自动 commit / close。"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """初始化数据库：执行 schema.sql + seed_data.sql。"""
    import os

    # 确保数据目录存在
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

    with get_db() as conn:
        # 建表
        if os.path.exists(SCHEMA_PATH):
            schema = open(SCHEMA_PATH, "r", encoding="utf-8").read()
            conn.executescript(schema)

        # 种子数据（幂等，用 INSERT OR REPLACE）
        if os.path.exists(SEED_PATH):
            seed = open(SEED_PATH, "r", encoding="utf-8").read()
            conn.executescript(seed)

    # 确保默认用户占位行存在（侧边栏表单需要 user_id='u001'）
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, student_name, major)
            VALUES ('u001', '', '')
        """)


# ── CRUD：用户 ──────────────────────────────────────

def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户信息。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_user(user_id: str, student_name: str, major: str,
                grade: str = None, available_slots: str = None,
                interests: str = None, has_minor: int = 0,
                minor_program: str = None) -> Dict[str, Any]:
    """插入或更新用户信息。"""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, student_name, major, grade,
                               available_slots, interests, has_minor, minor_program,
                               updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                student_name=excluded.student_name,
                major=excluded.major,
                grade=excluded.grade,
                available_slots=excluded.available_slots,
                interests=excluded.interests,
                has_minor=excluded.has_minor,
                minor_program=excluded.minor_program,
                updated_at=excluded.updated_at
        """, (user_id, student_name, major, grade,
              available_slots, interests, has_minor, minor_program))
    return get_user(user_id)


# ── CRUD：课程 ──────────────────────────────────────

def get_course(course_code: str) -> Optional[Dict[str, Any]]:
    """获取课程信息。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM courses WHERE course_code = ?", (course_code,)
        ).fetchone()
        return dict(row) if row else None


def list_courses(category: str = None, program: str = None) -> List[Dict[str, Any]]:
    """列出课程，支持按 category / program 过滤。"""
    with get_db() as conn:
        query = "SELECT * FROM courses WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if program:
            query += " AND program = ?"
            params.append(program)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def list_all_courses() -> List[Dict[str, Any]]:
    """列出全部课程。"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM courses").fetchall()
        return [dict(r) for r in rows]


# ── CRUD：课表 ──────────────────────────────────────

def get_timetable(user_id: str, course_type: str = None) -> List[Dict[str, Any]]:
    """获取用户课表。"""
    with get_db() as conn:
        if course_type:
            rows = conn.execute(
                "SELECT * FROM user_timetable WHERE user_id = ? AND course_type = ?",
                (user_id, course_type)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_timetable WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def add_timetable_entry(entry: Dict[str, Any]) -> int:
    """添加课表条目，返回自增 id。"""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO user_timetable
                (user_id, course_code, course_type, week_start, week_end,
                 day_of_week, period_start, period_end, location, exam_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (entry["user_id"], entry["course_code"], entry["course_type"],
              entry.get("week_start", 1), entry.get("week_end", 16),
              entry["day_of_week"], entry["period_start"], entry["period_end"],
              entry.get("location", ""), entry.get("exam_time", "")))
        return cur.lastrowid


def delete_timetable_entry(entry_id: int):
    """删除课表条目。"""
    with get_db() as conn:
        conn.execute("DELETE FROM user_timetable WHERE id = ?", (entry_id,))


def clear_user_minor_data(user_id: str):
    """清除用户辅修相关数据：课表条目 + 辅修状态。"""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM user_timetable WHERE user_id = ? AND course_type = 'minor'",
            (user_id,),
        )
        conn.execute(
            "UPDATE users SET has_minor = 0, minor_program = NULL, "
            "updated_at = datetime('now','localtime') WHERE user_id = ?",
            (user_id,),
        )


# ── CRUD：学习进度 ──────────────────────────────────

def get_study_progress(user_id: str, course_code: str = None) -> List[Dict[str, Any]]:
    """获取学习进度记录。"""
    with get_db() as conn:
        if course_code:
            rows = conn.execute(
                "SELECT * FROM study_progress WHERE user_id = ? AND course_code = ? ORDER BY week",
                (user_id, course_code)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM study_progress WHERE user_id = ? ORDER BY course_code, week",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def add_study_progress(entry: Dict[str, Any]) -> int:
    """添加学习进度记录。"""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO study_progress (user_id, course_code, week,
                homework_score, attendance, quiz_score, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (entry["user_id"], entry["course_code"], entry["week"],
              entry.get("homework_score"), entry.get("attendance"),
              entry.get("quiz_score"), entry.get("note", "")))
        return cur.lastrowid


# ── CRUD：冲突记录 ──────────────────────────────────

def add_conflict_record(entry: Dict[str, Any]) -> int:
    """添加冲突记录。"""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO conflict_history
                (user_id, conflict_type, severity, course_a, course_b,
                 overlap_detail, suggestion, user_decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (entry["user_id"], entry["conflict_type"], entry["severity"],
              entry["course_a"], entry["course_b"],
              entry.get("overlap_detail"), entry.get("suggestion"),
              entry.get("user_decision")))
        return cur.lastrowid


def get_conflict_history(user_id: str) -> List[Dict[str, Any]]:
    """获取用户冲突历史。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM conflict_history WHERE user_id = ? ORDER BY detected_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── CRUD：报告 ──────────────────────────────────────

def add_report(entry: Dict[str, Any]) -> int:
    """存档报告。"""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO reports (user_id, report_type, content_md, summary_json)
            VALUES (?, ?, ?, ?)
        """, (entry["user_id"], entry["report_type"],
              entry["content_md"], entry.get("summary_json")))
        return cur.lastrowid


def get_reports(user_id: str, report_type: str = None) -> List[Dict[str, Any]]:
    """获取用户的历史报告。"""
    with get_db() as conn:
        if report_type:
            rows = conn.execute(
                "SELECT * FROM reports WHERE user_id = ? AND report_type = ? ORDER BY generated_at DESC",
                (user_id, report_type)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY generated_at DESC",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]
