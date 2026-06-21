# 用户自助注册与信息录入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 替换硬编码的"张三"演示数据，让用户能自助输入个人信息（姓名、主修、年级、兴趣等），存入 SQLite，后续所有辅修推荐和资源推荐基于真实用户数据。

**Architecture:** 在 Streamlit 侧边栏新增"个人资料"编辑区（可折叠），通过已有的 `upsert_user()` 写入 SQLite。首次使用（无用户数据）时显示引导式表单。同时修复 `data_manager.py` 中 `修改信息` 命令的断裂路径，使聊天和表单两个入口都能更新用户信息。

**Tech Stack:** Streamlit (st.form, st.expander) + SQLite (已有 upsert_user/ get_user) + Python

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `streamlit_app.py` | 侧边栏新增个人资料编辑表单；移除 user_id 硬编码；首次引导 |
| 修改 | `src/agents/data_manager.py` | 修复 `修改信息` 命令解析 + 修复 `upsert_user` 缺少必填参数的 bug |
| 修改 | `data/database/seed_data.sql` | 移除"张三"演示用户，仅保留课程/课表种子数据（不含用户） |
| 创建 | `src/tools/profile_tools.py` | 提取公共的用户信息验证/格式化逻辑 |
| 修改 | `src/tools/state_tools.py` | 新增 `refresh_user_profile` 供侧边栏同步 |

---

### Task 1: 修复 data_manager.py 的 upsert_user 必填参数 bug

**Files:**
- Modify: `src/agents/data_manager.py:110-120`

**问题：** `upsert_user(user_id=user_id)` 缺少必填参数 `student_name` 和 `major`，运行时直接 `TypeError`。

- [ ] **Step 1: 读取当前代码确认问题**

读 [src/agents/data_manager.py](src/agents/data_manager.py) 第 110-125 行。

- [ ] **Step 2: 修复 — 先查后写**

```python
# 修复前（第 115-117 行）：
upsert_user(
    user_id=user_id,
)

# 修复后：
existing = get_user(user_id) or {}
upsert_user(
    user_id=user_id,
    student_name=existing.get("student_name", "未命名"),
    major=existing.get("major", "未设置"),
    grade=existing.get("grade"),
    available_slots=existing.get("available_slots"),
    interests=existing.get("interests"),
    has_minor=existing.get("has_minor", 0),
    minor_program=existing.get("minor_program"),
)
```

- [ ] **Step 3: 修复 `_show_and_prompt_info_edit` 和 `_continue_input` 的断裂路径**

在 `_continue_input` 方法（约第 89 行）中新增对 `修改信息` 格式的解析：

```python
# 在 _continue_input 方法开头加入（约第 89 行之后）
import re
PROFILE_EDIT_RE = re.compile(r'修改信息\s+(姓名|主修|年级|兴趣|name|major|grade|interests)\s+(.+)', re.I)

profile_match = PROFILE_EDIT_RE.match(user_input)
if profile_match:
    field = profile_match.group(1)
    value = profile_match.group(2).strip()
    return self._handle_profile_edit(user_id, field, value, state)
```

新增 `_handle_profile_edit` 方法：

```python
def _handle_profile_edit(self, user_id: str, field: str, value: str,
                          state: AgentState) -> AgentState:
    """处理单字段个人信息修改。"""
    FIELD_MAP = {
        "姓名": "student_name", "name": "student_name",
        "主修": "major", "major": "major",
        "年级": "grade", "grade": "grade",
        "兴趣": "interests", "interests": "interests",
    }
    db_field = FIELD_MAP.get(field)
    if not db_field:
        state["final_response"] = f"⚠️ 不支持的字段：{field}。可修改：姓名、主修、年级、兴趣"
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    existing = get_user(user_id) or {}
    updates = {
        "student_name": existing.get("student_name", ""),
        "major": existing.get("major", ""),
        "grade": existing.get("grade"),
        "available_slots": existing.get("available_slots"),
        "interests": existing.get("interests"),
        "has_minor": existing.get("has_minor", 0),
        "minor_program": existing.get("minor_program"),
    }
    updates[db_field] = value

    upsert_user(user_id=user_id, **updates)

    state["final_response"] = f"✅ 已更新 {field} 为：{value}"
    state["conversation_phase"] = ConversationPhase.IDLE.value
    return state
```

- [ ] **Step 4: 验证修复**

Run: `python -c "from src.agents.data_manager import DataManagerAgent; print('Import OK')"`

---

### Task 2: 创建 profile_tools.py — 公共验证/格式化

**Files:**
- Create: `src/tools/profile_tools.py`

- [ ] **Step 1: 创建文件**

```python
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
```

- [ ] **Step 2: 验证导入**

Run: `python -c "from src.tools.profile_tools import validate_profile, build_user_updates; print('OK')"`

---

### Task 3: Streamlit 侧边栏新增个人资料编辑

**Files:**
- Modify: `streamlit_app.py:113-134`

这是核心改动。将侧边栏从"只读展示"变为"可编辑表单"。

- [ ] **Step 1: 替换侧边栏用户信息区为可折叠编辑表单**

将原来 [streamlit_app.py:115-134](streamlit_app.py#L115-L134) 的只读展示替换为：

```python
with st.sidebar:
    st.title("🎓 学业规划助手")

    # ── 个人资料（可编辑）──
    user = get_user(USER_ID)

    with st.expander("👤 个人资料", expanded=(user is None)):
        if user is None:
            st.info("欢迎！请先填写你的基本信息。")

        with st.form("profile_form"):
            student_name = st.text_input(
                "姓名",
                value=user.get("student_name", "") if user else "",
                placeholder="请输入姓名",
            )
            col1, col2 = st.columns(2)
            with col1:
                major = st.selectbox(
                    "主修专业",
                    options=MAJOR_OPTIONS,
                    index=_major_index(user.get("major", "")) if user else 0,
                )
            with col2:
                grade = st.selectbox(
                    "年级",
                    options=GRADE_OPTIONS,
                    index=_grade_index(user.get("grade", "")) if user else 0,
                )

            # 兴趣标签
            current_interests = _parse_interests(user.get("interests", "")) if user else []
            interests_text = st.text_input(
                "兴趣方向（逗号分隔）",
                value="，".join(current_interests) if current_interests else "",
                placeholder="如：金融，数据科学，人工智能",
                help="用于辅修推荐和资源匹配",
            )

            submitted = st.form_submit_button("💾 保存", use_container_width=True)

            if submitted:
                ok, err = validate_profile(student_name, major)
                if not ok:
                    st.error(err)
                else:
                    updates = build_user_updates(
                        student_name=student_name,
                        major=major,
                        grade=grade,
                        interests_str=interests_text,
                        existing=user or {},
                    )
                    updates["user_id"] = USER_ID
                    upsert_user(**updates)
                    st.success("✅ 资料已保存！")
                    st.rerun()

    # 辅修状态（只读展示）
    if user:
        timetable = get_timetable(USER_ID)
        has_minor = user.get("has_minor", 0) == 1
        if not has_minor:
            has_minor = any(c.get("course_type") == "minor" for c in (timetable or []))
        if has_minor:
            st.success(f"📌 辅修: {user.get('minor_program') or '已选择'}")
        else:
            st.info("📌 辅修: 未选择")
    else:
        st.info("📌 辅修: 未选择")
```

- [ ] **Step 2: 添加辅助函数到 streamlit_app.py 顶部**

在 `_refresh_user_flags` 函数附近添加：

```python
def _major_index(major: str) -> int:
    """返回 major 在 MAJOR_OPTIONS 中的索引。"""
    try:
        return MAJOR_OPTIONS.index(major)
    except ValueError:
        return len(MAJOR_OPTIONS) - 1  # "其他"


def _grade_index(grade: str) -> int:
    """返回 grade 在 GRADE_OPTIONS 中的索引。"""
    try:
        return GRADE_OPTIONS.index(grade)
    except ValueError:
        return 0


def _parse_interests(interests_json: str) -> list:
    """解析 interests JSON 字符串为列表。"""
    import json
    try:
        data = json.loads(interests_json)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
```

- [ ] **Step 3: 添加缺失的 import**

在 `streamlit_app.py` 顶部 import 区域追加：

```python
from src.tools.profile_tools import MAJOR_OPTIONS, GRADE_OPTIONS, validate_profile, build_user_updates
```

确保 `upsert_user` 已导入（检查第 16-20 行的 import）：

```python
from src.storage.sqlite_client import (
    get_timetable, get_study_progress, get_conflict_history,
    upsert_user,  # ← 新增这个
)
```

等确认后再改。

- [ ] **Step 4: 验证导入链**

Run: `python -c "import streamlit_app"`（在 `streamlit run` 重启时自然验证）

---

### Task 4: 修改 seed_data.sql — 移除演示用户

**Files:**
- Modify: `data/database/seed_data.sql:7-16`

- [ ] **Step 1: 移除"张三"用户插入，保留课程和课表种子数据**

将 [data/database/seed_data.sql](data/database/seed_data.sql) 第 7-16 行的 `INSERT OR REPLACE INTO users` 改为**仅在用户表为空时插入默认空用户**（用于 Streamlit 首次加载时的 user_id = u001 回退）：

```sql
-- 默认用户（仅当用户表为空时使用；用户可通过侧边栏表单覆盖）
INSERT OR REPLACE INTO users (user_id, student_name, major, grade, available_slots, interests, has_minor, minor_program)
SELECT 'u001', '', '', NULL, NULL, NULL, 0, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE user_id = 'u001');
```

如果 ALTER 不允许这种写法，则删除原来的 INSERT 行（第 7-16 行），改用 Python 在 `init_database()` 中处理默认用户的创建。

- [ ] **Step 2: 同步修改 init_database() 确保 u001 占位行存在**

在 [src/storage/sqlite_client.py:43-60](src/storage/sqlite_client.py#L43-L60) 的 `init_database()` 末尾追加：

```python
# 确保默认用户占位行存在（侧边栏表单需要）
conn = get_db()
conn.execute("""
    INSERT OR IGNORE INTO users (user_id, student_name, major)
    VALUES ('u001', '', '')
""")
```

- [ ] **Step 3: 重置数据库验证**

```bash
rm -f data/database/study_agent.db
python -c "
from src.storage import init_database
from src.storage.sqlite_client import get_user
init_database()
u = get_user('u001')
print('user_id:', u['user_id'])
print('student_name:', repr(u['student_name']))  # 应该是 ''
print('major:', repr(u['major']))  # 应该是 ''
# 课表种子数据仍在
from src.storage.sqlite_client import get_timetable
tt = get_timetable('u001')
print('timetable entries:', len(tt))
"
```

Expected: `student_name=''`, `major=''`，课表种子数据完整。

---

### Task 5: 首次使用引导 — Streamlit 欢迎页

**Files:**
- Modify: `streamlit_app.py` — 在主聊天区顶部新增首次引导

- [ ] **Step 1: 在主聊天区新增首次引导提示**

在 "学业规划助手" 标题之后、消息渲染之前，插入：

```python
# 首次使用引导：用户名为空时显示
user = get_user(USER_ID)
if user and not user.get("student_name", "").strip():
    with st.container(border=True):
        st.markdown("### 👋 欢迎使用学业规划助手！")
        st.markdown(
            "请先在 **左侧边栏 → 👤 个人资料** 中填写你的基本信息，"
            "然后就可以开始：\n\n"
            "• 📋 **推荐辅修** — 根据你的兴趣推荐辅修方案\n"
            "• 📈 **查看进度** — 追踪各课程学习情况\n"
            "• 📚 **推荐资源** — 获取个性化学习资源\n"
            "• ⚠️ **检测冲突** — 检查主修/辅修时间冲突"
        )
```

- [ ] **Step 2: 验证渲染**

启动 Streamlit，确认首次（数据库无用户名时）显示引导面板，填写资料后引导消失。

---

### Task 6: 端到端集成测试

**Files:**
- Create: `tests/test_user_onboarding.py`

- [ ] **Step 1: 创建测试文件**

```python
"""测试用户自助注册流程。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from src.storage.sqlite_client import get_user, upsert_user, get_timetable
from src.tools.profile_tools import validate_profile, build_user_updates


def test_validate_profile_empty_name():
    ok, err = validate_profile("", "计算机科学与技术")
    assert not ok
    assert "姓名" in err


def test_validate_profile_empty_major():
    ok, err = validate_profile("张三", "")
    assert not ok
    assert "主修" in err


def test_validate_profile_ok():
    ok, err = validate_profile("张三", "计算机科学与技术")
    assert ok
    assert err is None


def test_build_user_updates():
    existing = {"user_id": "u001", "grade": "2022级"}
    result = build_user_updates(
        student_name="李四", major="金融学",
        grade="2024级", interests_str="金融,数据科学",
        existing=existing,
    )
    assert result["student_name"] == "李四"
    assert result["major"] == "金融学"
    assert result["grade"] == "2024级"
    assert result["has_minor"] == 0  # preserved from existing


def test_upsert_and_read_user():
    upsert_user(
        user_id="test_onboard",
        student_name="测试用户",
        major="测试专业",
        grade="2024级",
        interests='["金融","数据科学"]',
        has_minor=0,
    )
    u = get_user("test_onboard")
    assert u is not None
    assert u["student_name"] == "测试用户"
    assert u["major"] == "测试专业"
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_user_onboarding.py -v
```

Expected: 4 tests PASS

---

## 自审

**1. Spec 覆盖检查：**
- ✅ 用户输入姓名/主修/年级/兴趣 → Task 3（侧边栏表单）
- ✅ 存入数据库 → Task 3（upsert_user）+ Task 2（build_user_updates）
- ✅ 替换"张三" → Task 4（移除 seed 用户）
- ✅ 修复断裂的修改信息命令 → Task 1
- ✅ 首次使用引导 → Task 5
- ✅ 辅修推荐使用真实数据 → 已存在（course_planner 读 get_user）

**2. Placeholder 扫描：** 无 TBD/TODO。

**3. 类型一致性：** `validate_profile` 返回 `tuple[bool, Optional[str]]`，所有调用方正确解包。`build_user_updates` 返回 `Dict`，与 `upsert_user(**kwargs)` 兼容。

---

## 风险点

1. **seed_data.sql 中的 `WHERE NOT EXISTS` 语法** — SQLite 3.33+ 支持 `INSERT ... WHERE`，需确认 Python 环境中的 SQLite 版本 >= 3.33。
2. **st.form 嵌套在 st.expander 中** — Streamlit 1.30 支持此模式，已验证。
3. **表单提交触发 st.rerun()** — 需确保 rerun 不会导致表单数据丢失（Streamlit 的 form 会自动保留提交值到 session_state）。
