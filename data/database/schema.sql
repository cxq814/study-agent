-- ============================================================
-- 大学生辅修学习规划与跟踪多智能体系统 — 数据库建表语句
-- ============================================================

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    student_name    TEXT NOT NULL,
    major           TEXT NOT NULL,              -- 主修专业
    grade           TEXT,                       -- 年级，如 "2024级"
    available_slots TEXT,                       -- JSON: [{"day":1,"periods":[1,2,3]},...]
    interests       TEXT,                       -- JSON: ["计算机","金融"]
    has_minor       INTEGER DEFAULT 0,          -- 0/1
    minor_program   TEXT,                       -- 已选辅修专业名
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- 课程信息表
CREATE TABLE IF NOT EXISTS courses (
    course_code     TEXT PRIMARY KEY,           -- "CS101"
    course_name     TEXT NOT NULL,
    credits         REAL,
    difficulty      TEXT,                       -- "入门" / "中级" / "高级"
    category        TEXT,                       -- "主修" / "辅修" / "通识"
    program         TEXT,                       -- 所属专业
    semester        TEXT,                       -- 开课学期
    description     TEXT,                       -- 课程简介
    prerequisites   TEXT,                       -- JSON: ["CS100"]
    capacity        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- 用户课表
CREATE TABLE IF NOT EXISTS user_timetable (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    course_code     TEXT NOT NULL,
    course_type     TEXT NOT NULL,              -- "major" / "minor"
    week_start      INTEGER DEFAULT 1,
    week_end        INTEGER DEFAULT 16,
    day_of_week     INTEGER,                   -- 1-7（周日=7）
    period_start    INTEGER,                   -- 起始节次 1-12
    period_end      INTEGER,                   -- 结束节次
    location        TEXT,
    exam_time       TEXT,                      -- ISO 格式，可为 NULL
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (course_code) REFERENCES courses(course_code)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_timetable_unique
    ON user_timetable(user_id, course_code, course_type);

-- 学习进度
CREATE TABLE IF NOT EXISTS study_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    course_code     TEXT NOT NULL,
    week            INTEGER,
    homework_score  REAL,
    attendance      TEXT,                      -- "present" / "absent" / "late"
    quiz_score      REAL,
    note            TEXT,
    recorded_at     TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (course_code) REFERENCES courses(course_code)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_progress_unique
    ON study_progress(user_id, course_code, week);

-- 冲突检测历史
CREATE TABLE IF NOT EXISTS conflict_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    conflict_type   TEXT NOT NULL,             -- "course_overlap" / "exam_overlap"
    severity        TEXT NOT NULL,             -- "critical" / "warning" / "info"
    course_a        TEXT NOT NULL,
    course_b        TEXT NOT NULL,
    overlap_detail  TEXT,                      -- JSON
    suggestion      TEXT,
    user_decision   TEXT,
    detected_at     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_unique
    ON conflict_history(user_id, course_a, course_b, conflict_type);

-- 报告存档
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    report_type     TEXT NOT NULL,             -- "weekly" / "monthly" / "semester"
    content_md      TEXT NOT NULL,
    summary_json    TEXT,
    generated_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reports_lookup
    ON reports(user_id, report_type);
