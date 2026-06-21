"""
SQLite 数据模型（Pydantic）。

用于 Python → JSON ↔ SQLite 的数据序列化与校验。
"""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class User(BaseModel):
    user_id: str
    student_name: str
    major: str
    grade: Optional[str] = None
    available_slots: Optional[str] = None   # JSON string
    interests: Optional[str] = None         # JSON string
    has_minor: int = 0
    minor_program: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Course(BaseModel):
    course_code: str
    course_name: str
    credits: float = 0.0
    difficulty: Optional[str] = None
    category: Optional[str] = None
    program: Optional[str] = None
    semester: Optional[str] = None
    description: Optional[str] = None
    prerequisites: Optional[str] = None    # JSON string
    capacity: int = 0
    created_at: Optional[str] = None


class TimetableEntry(BaseModel):
    id: Optional[int] = None
    user_id: str
    course_code: str
    course_type: str                       # "major" / "minor"
    week_start: int = 1
    week_end: int = 16
    day_of_week: int
    period_start: int
    period_end: int
    location: Optional[str] = None
    exam_time: Optional[str] = None


class StudyProgress(BaseModel):
    id: Optional[int] = None
    user_id: str
    course_code: str
    week: int
    homework_score: Optional[float] = None
    attendance: Optional[str] = None       # "present" / "absent" / "late"
    quiz_score: Optional[float] = None
    note: Optional[str] = None
    recorded_at: Optional[str] = None


class ConflictRecord(BaseModel):
    id: Optional[int] = None
    user_id: str
    conflict_type: str
    severity: str
    course_a: str
    course_b: str
    overlap_detail: Optional[str] = None   # JSON
    suggestion: Optional[str] = None
    user_decision: Optional[str] = None
    detected_at: Optional[str] = None


class Report(BaseModel):
    id: Optional[int] = None
    user_id: str
    report_type: str
    content_md: str
    summary_json: Optional[str] = None
    generated_at: Optional[str] = None
