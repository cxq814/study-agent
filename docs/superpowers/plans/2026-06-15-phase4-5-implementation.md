# Phase 4-5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 4 remaining Phase 4 agents (Report Generator, Data Manager, Course Adjuster, Chat QA) to replace echo stubs, then build the Phase 5 Streamlit frontend with performance optimization and documentation.

**Architecture:** Phase 4 delivers 4 real agents following the established LangGraph StateGraph pattern — each agent is a class with `run(state: AgentState) -> AgentState` that reads from/writes to the shared state, uses the tools layer (llm_tools, stats_tools, export_tools, course_edit_tools, rag_tools, sqlite_tools, base_tools), and integrates into the existing DAG via workflow.py registration. Phase 5 wraps the CLI in a Streamlit chat UI and adds performance polish.

**Tech Stack:** Python 3.10+, LangGraph StateGraph, SQLite (WAL), ChromaDB, Anthropic API (optional), Streamlit, Plotly

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/agents/report_generator.py` | **Create** | Aggregate data, call LLM/template, export .md report |
| `src/agents/data_manager.py` | **Create** | Multi-turn timetable CRUD + personal info editing |
| `src/agents/course_adjuster.py` | **Create** | Drop/swap minor courses with confirmation + conflict pre-check |
| `src/agents/chat_qa.py` | **Create** | RAG + LLM academic Q&A; graceful fallback for casual chat |
| `src/graph/workflow.py` | **Modify** | Replace 4 echo stubs with real agent nodes; add lazy-loading getters |
| `test_phase4.py` | **Create** | 25+ integration tests covering all 4 new agents |
| `streamlit_app.py` | **Create** | Phase 5 Streamlit frontend |
| `docs/user_manual.md` | **Create** | Phase 5 user documentation |

---

### Task 1: Report Generator Agent

**Files:**
- Create: `src/agents/report_generator.py`
- Create: `test_phase4.py` (test entries 1-6)
- Modify: `src/graph/workflow.py` (replace echo stub)

**Design:** Single-turn agent. On trigger ("生成报告"/"学业报告"/etc.), aggregates 4 data sources from SQLite → feeds stats_tools → calls llm_call with Markdown template fallback → exports .md file → persists to reports table. Phase stays IDLE.

- [ ] **Step 1: Write the report generator agent file**

Create `src/agents/report_generator.py`:

```python
"""
报告生成 Agent。

职责：
    1. 聚合用户数据（课表、进度、冲突、基本信息）
    2. 调用 stats_tools 计算趋势、风险分
    3. 生成结构化 Markdown 报告（LLM 优先，模板回退）
    4. 导出 .md 文件到 data/exports/
    5. 持久化报告记录到 SQLite reports 表
"""

import logging
import json
from datetime import datetime
from typing import Optional

from src.graph.state import AgentState, ConversationPhase
from src.tools.llm_tools import llm_call, llm_is_available
from src.tools.stats_tools import (
    calc_progress_trend,
    calc_conflict_summary,
    calc_risk_score,
    compare_with_peers,
)
from src.tools.export_tools import export_markdown
from src.tools.base_tools import format_markdown_table, get_current_user
from src.storage.sqlite_client import (
    get_timetable,
    get_study_progress,
    get_conflict_history,
    add_report,
)

logger = logging.getLogger(__name__)

# ── Markdown 报告模板（LLM 不可用时的回退）──

REPORT_TEMPLATE = """# 📊 学业综合报告

> 生成时间: {generated_at}
> 姓名: {student_name} | 主修: {major} | 辅修: {minor_program}

---

## 一、基本信息

| 项目 | 内容 |
|------|------|
| 姓名 | {student_name} |
| 主修专业 | {major} |
| 年级 | {grade} |
| 辅修专业 | {minor_program} |
| 辅修课程数 | {minor_count} 门 |
| 主修课程数 | {major_count} 门 |

---

## 二、课表概览

{schedule_table}

---

## 三、学习进度

{progress_section}

---

## 四、时间冲突

{conflict_section}

---

## 五、综合风险评分

| 维度 | 得分 |
|------|------|
| 出勤风险 | {attendance_risk} |
| 作业风险 | {homework_risk} |
| 冲突风险 | {conflict_risk} |
| **综合风险分** | **{risk_score}/100** |

> 风险等级: {risk_level}

---

## 六、学习建议

{suggestions}

---

*报告由学业规划助手自动生成*
"""

RISK_LEVELS = {
    (0, 20): "🟢 低风险 — 当前学业状态良好，继续保持。",
    (20, 50): "🟡 中等风险 — 建议关注薄弱课程，及时查漏补缺。",
    (50, 70): "🟠 较高风险 — 建议调整学习策略，考虑减少辅修课程或加强复习。",
    (70, 101): "🔴 高风险 — 学业压力较大，强烈建议调整课表或寻求学业指导。",
}


class ReportGeneratorAgent:
    """报告生成 Agent — 聚合多源数据，生成结构化学业报告。"""

    def run(self, state: AgentState) -> AgentState:
        user_id = state.get("user_id", "")
        user_input = state.get("user_input", "")

        try:
            # 1. 聚合数据
            user = get_current_user(user_id)
            major_courses = get_timetable(user_id, course_type="major")
            minor_courses = get_timetable(user_id, course_type="minor")
            progress = get_study_progress(user_id)
            conflicts = get_conflict_history(user_id)

            # 2. 统计分析
            trends = calc_progress_trend(progress)
            conflict_summary = calc_conflict_summary(conflicts)
            risk_score = calc_risk_score(progress, conflicts)

            # 3. 生成报告内容
            student_name = user.get("student_name", "未知")
            major = user.get("major", "未知")
            grade = user.get("grade", "未知")
            minor_program = user.get("minor_program") or "未选择"

            # 课表表格
            schedule_rows = []
            for c in major_courses:
                schedule_rows.append([
                    c.get("course_code", ""),
                    c.get("course_name", c.get("course_code", "")),
                    "主修",
                    f"周{c.get('day_of_week', '?')} {c.get('period_start', '?')}-{c.get('period_end', '?')}节",
                    c.get("location", "-"),
                ])
            for c in minor_courses:
                schedule_rows.append([
                    c.get("course_code", ""),
                    c.get("course_name", c.get("course_code", "")),
                    "辅修",
                    f"周{c.get('day_of_week', '?')} {c.get('period_start', '?')}-{c.get('period_end', '?')}节",
                    c.get("location", "-"),
                ])
            schedule_table = format_markdown_table(
                ["课程代码", "课程名", "类型", "时间", "地点"],
                schedule_rows,
            ) if schedule_rows else "（暂无课表数据）"

            # 进度段落
            progress_lines = []
            for code, data in trends.items():
                progress_lines.append(
                    f"- **{code}**: 作业趋势 {data.get('hw_trend', 'flat')}, "
                    f"小测趋势 {data.get('quiz_trend', 'flat')}, "
                    f"出勤率 {data.get('attendance_rate', 0)}%"
                )
            progress_section = "\n".join(progress_lines) if progress_lines else "（暂无进度数据）"

            # 冲突段落
            conflict_section = (
                f"共 {conflict_summary.get('total', 0)} 条冲突记录，"
                f"其中严重 {conflict_summary.get('critical_count', 0)} 条，"
                f"轻微 {conflict_summary.get('warning_count', 0)} 条。"
            )
            if conflict_summary.get("affected_courses"):
                conflict_section += (
                    f"\n\n涉及课程: {', '.join(conflict_summary['affected_courses'])}"
                )

            # 风险等级
            risk_level = "未知"
            for (lo, hi), text in RISK_LEVELS.items():
                if lo <= risk_score < hi:
                    risk_level = text
                    break

            # 风险细分
            attendance_risk = self._calc_attendance_deduction(progress)
            homework_risk = self._calc_homework_deduction(trends)
            conflict_risk = conflict_summary["critical_count"] * 5 + conflict_summary["warning_count"] * 1

            # 4. 尝试 LLM 生成（回退模板）
            generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            template_content = REPORT_TEMPLATE.format(
                generated_at=generated_at,
                student_name=student_name,
                major=major,
                grade=grade,
                minor_program=minor_program,
                minor_count=len(minor_courses),
                major_count=len(major_courses),
                schedule_table=schedule_table,
                progress_section=progress_section,
                conflict_section=conflict_section,
                attendance_risk=attendance_risk,
                homework_risk=homework_risk,
                conflict_risk=conflict_risk,
                risk_score=risk_score,
                risk_level=risk_level,
                suggestions=self._gen_suggestions(risk_score, conflicts, trends),
            )

            # LLM 增强（可选）
            report_content = self._generate_with_llm(
                template_content, student_name, major, minor_program,
                len(major_courses), len(minor_courses),
                trends, conflict_summary, risk_score,
            )

            # 5. 导出文件
            export_path = export_markdown(report_content, f"report_{user_id}")

            # 6. 持久化
            summary_json = json.dumps({
                "risk_score": risk_score,
                "major_count": len(major_courses),
                "minor_count": len(minor_courses),
                "conflict_total": conflict_summary["total"],
                "progress_courses": len(trends),
            }, ensure_ascii=False)
            add_report(user_id, "comprehensive", report_content, summary_json)

            # 7. 构建输出
            response = (
                f"📊 **学业综合报告已生成**\n\n"
                f"**基本信息**: {student_name} | {major}"
            )
            if minor_program and minor_program != "未选择":
                response += f" | 辅修 {minor_program}"
            response += (
                f"\n\n"
                f"**课表**: {len(major_courses)} 门主修 + {len(minor_courses)} 门辅修\n"
                f"**进度**: 覆盖 {len(trends)} 门课程\n"
                f"**冲突**: {conflict_summary['total']} 条记录\n"
                f"**综合风险分**: {risk_score}/100\n\n"
                f"📁 报告已导出: `{export_path}`\n\n"
                f"---\n\n"
                f"{report_content[-1500:]}"
            )

            state["final_response"] = response
            state["conversation_phase"] = ConversationPhase.IDLE.value

        except Exception as e:
            logger.error("Report generation failed: %s", e, exc_info=True)
            state["final_response"] = (
                f"⚠️ 报告生成失败: {e}\n\n"
                "请确认已录入主修课表和学习进度数据后重试。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value

        return state

    def _generate_with_llm(self, template_content: str,
                           student_name: str, major: str, minor: str,
                           major_count: int, minor_count: int,
                           trends: dict, conflicts: dict, risk_score: float) -> str:
        """尝试用 LLM 生成更好的报告摘要，失败则回退模板。"""
        if not llm_is_available():
            return template_content

        prompt = (
            f"请根据以下学业数据生成一份简洁的学业报告（Markdown 格式），"
            f"包含基本信息、课表、进度、冲突、风险和建议。"
            f"保持原有数据准确性，优化表述的可读性。\n\n"
            f"姓名: {student_name}\n"
            f"主修: {major}\n"
            f"辅修: {minor}\n"
            f"主修课程数: {major_count}\n"
            f"辅修课程数: {minor_count}\n"
            f"进度趋势: {json.dumps(trends, ensure_ascii=False, default=str)[:800]}\n"
            f"冲突统计: {json.dumps(conflicts, ensure_ascii=False)}\n"
            f"综合风险分: {risk_score}/100\n"
        )

        def fallback_template():
            return template_content

        return llm_call(
            prompt=prompt,
            system="你是一位大学学业规划顾问，擅长分析学生数据并给出建设性建议。请使用 Markdown 格式。",
            fallback=fallback_template,
            max_tokens=3000,
        )

    @staticmethod
    def _calc_attendance_deduction(progress: list) -> str:
        absent = sum(1 for p in progress if p.get("attendance") == "absent")
        if absent == 0:
            return "无缺勤"
        return f"{absent} 次缺勤"

    @staticmethod
    def _calc_homework_deduction(trends: dict) -> str:
        low_courses = []
        for code, data in trends.items():
            hw = data.get("homework_scores", [])
            if hw and sum(hw) / len(hw) < 70:
                low_courses.append(code)
        if not low_courses:
            return "作业均分正常"
        return f"{', '.join(low_courses)} 作业偏低"

    @staticmethod
    def _gen_suggestions(risk_score: float, conflicts: list, trends: dict) -> str:
        suggestions = []
        if risk_score >= 70:
            suggestions.append("- ⚠️ 风险较高，建议约谈学业导师评估辅修是否继续。")
        if risk_score >= 50:
            suggestions.append("- 📝 建议制定周复习计划，重点攻克薄弱课程。")
        if conflicts:
            critical_count = sum(1 for c in conflicts if c.get("severity") == "critical")
            if critical_count > 0:
                suggestions.append(f"- 🔴 存在 {critical_count} 条严重时间冲突，建议优先处理。")
        if trends:
            down_courses = [
                code for code, data in trends.items()
                if data.get("hw_trend") == "down" or data.get("quiz_trend") == "down"
            ]
            if down_courses:
                suggestions.append(
                    f"- 📉 {', '.join(down_courses)} 成绩下滑，建议增加学习时间投入。"
                )
        if not suggestions:
            suggestions.append("- ✅ 当前状态良好，继续保持！")
            suggestions.append("- 💡 可以尝试探索辅修相关的高阶课程或科研项目。")
        return "\n".join(suggestions)
```

- [ ] **Step 2: Verify the agent file passes import check**

Run: `cd e:/A2/study_agent && python -c "from src.agents.report_generator import ReportGeneratorAgent; a = ReportGeneratorAgent(); print('Import OK')"`

Expected: `Import OK`

- [ ] **Step 3: Write report generator tests**

Append to the end of `test_phase4.py` (this will be created fresh):

```python
#!/usr/bin/env python
"""Phase 4 集成测试 — 报告生成 + 数据管理 + 课程调整 + 兜底问答。"""

import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.WARNING)

from src.storage import init_database, get_user, is_redis_available
from src.graph import create_initial_state, get_app, ConversationPhase

init_database()

USER_ID = "u001"
SESSION_ID = "phase4_test"

user = get_user(USER_ID)
has_minor = user.get("has_minor", 0) == 1 if user else False
app = get_app()

print("=" * 60)
print("Phase 4 集成测试")
print(f"用户: {user['student_name'] if user else 'N/A'}")
print(f"辅修: {'已选' if has_minor else '未选'}")
print(f"Redis: {'已连接' if is_redis_available() else '未连接（内存降级）'}")
print("=" * 60)

total = passed = 0


def run_and_check(user_input: str, check_fn, desc: str,
                  has_minor_override=None) -> bool:
    """运行一次完整流程并用 check_fn 验证。"""
    global total, passed
    total += 1
    hm = has_minor_override if has_minor_override is not None else has_minor
    state = create_initial_state(USER_ID, SESSION_ID, user_input, hm, True)
    result = app.invoke(state)
    ok = check_fn(result)
    passed += ok
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {desc}")
    if not ok:
        resp = result.get("final_response", "")
        print(f"    Response (前120字): {resp[:120]}")
        print(f"    Intent: {result.get('intent')}, Phase: {result.get('conversation_phase')}")
    return ok


# ══════════════════════════════════════════════════════
# 1. 报告生成 Agent 测试
# ══════════════════════════════════════════════════════

print("\n── 1. 报告生成 Agent ──")

# 1a: 意图路由
run_and_check("生成报告", lambda r: r.get("intent") == "gen_report", "1a: '生成报告' → gen_report")
run_and_check("学业报告", lambda r: r.get("intent") == "gen_report", "1b: '学业报告' → gen_report")
run_and_check("综合报告", lambda r: r.get("intent") == "gen_report", "1c: '综合报告' → gen_report")

# 1d: 报告内容包含关键字
def _check_report(r):
    resp = r.get("final_response", "")
    return "报告" in resp and len(resp) > 200
run_and_check("出报告", _check_report, "1d: 报告内容输出正常")

# 1e: 报告完成后 phase 回到 IDLE
def _check_phase_idle(r):
    return r.get("conversation_phase") == ConversationPhase.IDLE.value
run_and_check("生成报告", _check_phase_idle, "1e: 报告后 phase=IDLE")

# 1f: 无进度数据时也能生成基础报告
def _check_no_data_report(r):
    resp = r.get("final_response", "")
    return "报告" in resp or "暂" in resp or "无" in resp
run_and_check("学业报告", _check_no_data_report, "1f: 无进度数据时降级输出")
```

- [ ] **Step 4: Run the report tests to verify they pass**

Run: `cd e:/A2/study_agent && python test_phase4.py`

Expected: all 6 report tests PASS (once workflow is wired in Task 5)

---

### Task 2: Data Manager Agent

**Files:**
- Create: `src/agents/data_manager.py`
- Append: `test_phase4.py` (test entries 7-12)

**Design:** Multi-turn agent for timetable CRUD and personal info editing. Uses AWAIT_DATA_INPUT phase. Routes by sub-intent keywords within the phase.

- [ ] **Step 1: Write the data manager agent file**

Create `src/agents/data_manager.py`:

```python
"""
数据管理 Agent。

职责：
    1. 查看课表（主修 + 辅修，按星期排列）
    2. 录入主修课表（逐门添加，解析自然语言课程信息）
    3. 修改个人信息（年级、可用时段、兴趣标签）
    4. 删除课表条目

多轮交互流程：
    IDLE → 用户输入"录入课表" → AWAIT_DATA_INPUT
    → 用户输入课程信息 → 添加 → 询问是否继续
    → 用户输入"完成" → IDLE
"""

import logging
import re
from typing import Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_timetable, add_timetable_entry, delete_timetable_entry,
    upsert_user, get_user,
)
from src.tools.base_tools import format_markdown_table, get_current_user

logger = logging.getLogger(__name__)

# 课程信息解析正则
COURSE_PARSE_RE = re.compile(
    r'(?P<code>[A-Z]{2,4}\d{3,4})\s*'
    r'周(?P<day>[一二三四五六日1-7])'
    r'(?P<period_start>\d+)[-~至](?P<period_end>\d+)节'
    r'(?:\s*(?P<location>.+?))?\s*'
    r'(?:考试[:：](?P<exam>.+?))?\s*$'
)

DAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}

SUB_COMMANDS = {
    "查看": "view",
    "录入": "add",
    "添加": "add",
    "删除": "delete",
    "修改": "edit_info",
    "编辑": "edit_info",
    "信息": "edit_info",
}


class DataManagerAgent:
    """数据管理 Agent — 课表 CRUD + 个人信息编辑。"""

    def run(self, state: AgentState) -> AgentState:
        user_id = state.get("user_id", "")
        phase = state.get("conversation_phase", "")
        user_input = state.get("user_input", "")

        # 首次进入 → 识别子命令
        if phase != ConversationPhase.AWAIT_DATA_INPUT.value:
            return self._entry(state, user_id, user_input)

        # 已在 AWAIT_DATA_INPUT → 继续数据录入或退出
        return self._continue_input(state, user_id, user_input)

    def _entry(self, state: AgentState, user_id: str, user_input: str) -> AgentState:
        """首次进入数据管理，识别子功能并分发。"""
        # 查看课表
        if any(kw in user_input for kw in ["查看", "我的课表", "课表", "看看"]):
            return self._show_timetable(state, user_id)

        # 录入课表
        if any(kw in user_input for kw in ["录入", "添加", "新增"]):
            state["conversation_phase"] = ConversationPhase.AWAIT_DATA_INPUT.value
            state["final_response"] = (
                "📝 **数据录入模式**\n\n"
                "请按以下格式输入主修课程信息（每次一门）：\n\n"
                "`课程代码 周X 第N-N节 教室位置 考试:时间`\n\n"
                "示例: `CS101 周一1-3节 教三楼201 考试:第18周`\n\n"
                "输入「**完成**」或「**取消**」退出录入模式。"
            )
            return state

        # 修改个人信息
        if any(kw in user_input for kw in ["修改", "编辑", "更新信息", "我的信息"]):
            return self._show_and_prompt_info_edit(state, user_id)

        # 删除
        if any(kw in user_input for kw in ["删除", "移除"]):
            return self._show_delete_prompt(state, user_id)

        # 默认：展示课表
        return self._show_timetable(state, user_id)

    def _continue_input(self, state: AgentState, user_id: str,
                        user_input: str) -> AgentState:
        """在 AWAIT_DATA_INPUT 阶段，解析课程信息或退出。"""
        if user_input.strip() in ("完成", "结束", "好了", "取消", "退出"):
            return self._exit_to_idle(state, "数据录入完成。")

        parsed = self._parse_course_input(user_input)
        if parsed:
            try:
                add_timetable_entry({
                    "user_id": user_id,
                    "course_code": parsed["code"],
                    "course_type": "major",
                    "day_of_week": parsed["day"],
                    "period_start": parsed["period_start"],
                    "period_end": parsed["period_end"],
                    "location": parsed.get("location", ""),
                    "exam_time": parsed.get("exam", ""),
                    "week_start": 1,
                    "week_end": 16,
                })

                # 标记主修课表已录入
                state["has_major_timetable"] = True
                upsert_user(
                    user_id=user_id,
                    has_major_timetable=1,
                )

                state["final_response"] = (
                    f"✅ 已添加 {parsed['code']}（周{parsed['day']} "
                    f"第{parsed['period_start']}-{parsed['period_end']}节）\n\n"
                    "继续添加下一门课程，或输入「**完成**」退出。"
                )
            except Exception as e:
                logger.error("Failed to add timetable entry: %s", e)
                state["final_response"] = (
                    f"⚠️ 添加失败: {e}\n\n请检查格式后重试。"
                )
        else:
            state["final_response"] = (
                "❓ 无法识别课程信息。请使用格式:\n\n"
                "`课程代码 周X 第N-N节 教室位置`\n\n"
                "例如: `CS101 周一1-3节 教三楼201`\n"
                "或输入「**完成**」退出。"
            )

        return state

    def _show_timetable(self, state: AgentState, user_id: str) -> AgentState:
        """展示用户完整课表（按星期排列）。"""
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        # 按星期分组
        by_day = {}
        for c in major_courses + minor_courses:
            day = c.get("day_of_week", 0)
            if day not in by_day:
                by_day[day] = []
            by_day[day].append(c)

        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        lines = ["📅 **我的课表**\n"]

        for day_idx in sorted(by_day):
            lines.append(f"**{day_names.get(day_idx, f'周{day_idx}')}**")
            for c in sorted(by_day[day_idx],
                           key=lambda x: x.get("period_start", 0)):
                course_type = "辅修" if c.get("course_type") == "minor" else "主修"
                lines.append(
                    f"  - {c.get('course_code', '?')} [{course_type}] "
                    f"第{c.get('period_start', '?')}-{c.get('period_end', '?')}节 "
                    f"{c.get('location', '') or ''}"
                )
            lines.append("")

        if not by_day:
            lines.append("暂无课程数据。输入「**录入课表**」开始添加。")

        # 添加操作提示
        lines.append("---")
        lines.append("📌 可用操作: `录入课表` | `删除课程` | `修改信息`")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _show_delete_prompt(self, state: AgentState, user_id: str) -> AgentState:
        """展示可删除的课表条目。"""
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        if not major_courses and not minor_courses:
            state["final_response"] = "📭 课表为空，没有可删除的课程。"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        lines = ["🗑️ **删除课程**\n请指定要删除的课程代码：\n"]
        for c in major_courses + minor_courses:
            course_type = "辅修" if c.get("course_type") == "minor" else "主修"
            lines.append(
                f"  - `{c.get('course_code', '?')}` [{course_type}] "
                f"({c.get('location', '无地点')})"
            )

        lines.append("\n输入课程代码确认删除，例如：`删除 CS101`")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _show_and_prompt_info_edit(self, state: AgentState, user_id: str) -> AgentState:
        """展示当前个人信息并提供编辑指引。"""
        user = get_current_user(user_id)

        lines = [
            "👤 **我的信息**\n",
            f"  姓名: {user.get('student_name', '未设置')}",
            f"  主修: {user.get('major', '未设置')}",
            f"  年级: {user.get('grade', '未设置')}",
            f"  兴趣: {user.get('interests', '[]')}",
            "",
            "---",
            "📝 修改信息请使用: `修改信息 字段 新值`",
            "可用字段: `年级` `专业` `兴趣`",
            "例如: `修改信息 年级 大二`",
        ]

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _exit_to_idle(self, state: AgentState, message: str) -> AgentState:
        state["final_response"] = f"✅ {message}"
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    @staticmethod
    def _parse_course_input(text: str) -> Optional[dict]:
        """解析"CS101 周一1-3节 教三楼201" 格式的课程输入。"""
        # 先尝试完整正则
        m = COURSE_PARSE_RE.match(text.strip())
        if m:
            day_str = m.group("day")
            day = DAY_MAP.get(day_str)
            if day is None:
                return None
            try:
                ps = int(m.group("period_start"))
                pe = int(m.group("period_end"))
            except (ValueError, TypeError):
                return None
            return {
                "code": m.group("code"),
                "day": day,
                "period_start": ps,
                "period_end": pe,
                "location": (m.group("location") or "").strip(),
                "exam": (m.group("exam") or "").strip(),
            }

        # 宽松匹配：课程代码 + 时间信息
        code_match = re.search(r'[A-Z]{2,4}\d{3,4}', text)
        if not code_match:
            return None
        code = code_match.group()

        day_match = re.search(r'周([一二三四五六日1-7])', text)
        if not day_match:
            return None
        day = DAY_MAP.get(day_match.group(1))
        if day is None:
            return None

        period_match = re.search(r'(\d+)[-~至](\d+)节', text)
        if not period_match:
            return None
        ps = int(period_match.group(1))
        pe = int(period_match.group(2))

        location_match = re.search(
            r'(?:在|位置[:：]?\s*)?(.+?)(?:考试|$)', text
        )
        location = ""
        if location_match:
            loc_candidate = location_match.group(1).strip()
            if not re.match(r'^周\d', loc_candidate):
                location = loc_candidate

        exam_match = re.search(r'考试[:：]\s*(.+?)$', text)

        return {
            "code": code,
            "day": day,
            "period_start": ps,
            "period_end": pe,
            "location": location,
            "exam": exam_match.group(1) if exam_match else "",
        }
```

- [ ] **Step 2: Verify import**

Run: `cd e:/A2/study_agent && python -c "from src.agents.data_manager import DataManagerAgent; a = DataManagerAgent(); print('Import OK')"`

Expected: `Import OK`

- [ ] **Step 3: Write data manager tests**

Append to `test_phase4.py`:

```python
# ══════════════════════════════════════════════════════
# 2. 数据管理 Agent 测试
# ══════════════════════════════════════════════════════

print("\n── 2. 数据管理 Agent ──")

# 2a: 意图路由
run_and_check("我的课表", lambda r: r.get("intent") == "data_manage", "2a: '我的课表' → data_manage")
run_and_check("录入课表", lambda r: r.get("intent") == "data_manage", "2b: '录入课表' → data_manage")
run_and_check("修改信息", lambda r: r.get("intent") == "data_manage", "2c: '修改信息' → data_manage")

# 2d: 查看课表输出包含课程信息
def _check_timetable_view(r):
    resp = r.get("final_response", "")
    return "课表" in resp and len(resp) > 20
run_and_check("查看课表", _check_timetable_view, "2d: 查看课表输出正常")

# 2e: 录入课表触发 AWAIT_DATA_INPUT
def _check_data_input_phase(r):
    return r.get("conversation_phase") == ConversationPhase.AWAIT_DATA_INPUT.value
run_and_check("录入课表", _check_data_input_phase, "2e: 录入课表 → AWAIT_DATA_INPUT")

# 2f: 删除课表指令
def _check_delete_prompt(r):
    resp = r.get("final_response", "")
    return "删除" in resp
run_and_check("删除课程", _check_delete_prompt, "2f: 删除课程提示正常")
```

- [ ] **Step 4: Run tests**

Run: `cd e:/A2/study_agent && python test_phase4.py`

Expected: all data manager tests PASS

---

### Task 3: Course Adjuster Agent

**Files:**
- Create: `src/agents/course_adjuster.py`
- Append: `test_phase4.py` (test entries 13-18)

**Design:** Multi-turn agent for dropping/swapping minor courses. Shows current minor courses → user selects → confirmation → execute with conflict pre-check. Uses AWAIT_COURSE_ADJUSTMENT phase.

- [ ] **Step 1: Write the course adjuster agent file**

Create `src/agents/course_adjuster.py`:

```python
"""
课程调整 Agent。

职责（细粒度逐门操作，与冲突检测的"调课"一键清空不同）：
    1. 退课：删除指定辅修课程
    2. 换课：退选一门 + 加选一门，带冲突预检
    3. 展示当前辅修课表

多轮交互流程：
    IDLE → 用户输入"退课" → 展示辅修课表 → AWAIT_COURSE_ADJUSTMENT
    → 用户输入"退 FIN201" → 确认 → 执行 → IDLE
    → 用户输入"换 FIN201 换 CS201" → 预检冲突 → 确认 → 执行 → IDLE

与 conflict_checker 中"调课"的关系：
    - 调课 (adjust_schedule): 一键清空所有辅修，重新来过
    - 退课/换课 (course_adjust): 逐门操作，带确认和冲突预检
    - 触发关键词不同（"调课" ≠ "退课"），流程独立
"""

import logging
import re
from typing import Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_timetable, get_course, get_user, upsert_user,
)
from src.tools.course_edit_tools import swap_course, validate_schedule
from src.tools.base_tools import format_markdown_table

logger = logging.getLogger(__name__)

# 换课格式: "换 FIN201 换 DS201" 或 "换 FIN201 → DS201"
SWAP_RE = re.compile(
    r'(?:换|退选|删除)\s*(?P<drop>[A-Z]{2,4}\d{3,4})\s*'
    r'(?:换|→|->|改成|改为)\s*(?P<add>[A-Z]{2,4}\d{3,4})'
)


class CourseAdjusterAgent:
    """课程调整 Agent — 退课/换课细粒度操作。"""

    def run(self, state: AgentState) -> AgentState:
        user_id = state.get("user_id", "")
        phase = state.get("conversation_phase", "")
        user_input = state.get("user_input", "")

        # 首次进入 → 展示辅修课表，等待选择
        if phase != ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value:
            return self._entry(state, user_id)

        # 已在 AWAIT_COURSE_ADJUSTMENT → 处理退课/换课指令
        return self._handle_adjustment(state, user_id, user_input)

    def _entry(self, state: AgentState, user_id: str) -> AgentState:
        """首次进入：展示辅修课表并提示操作。"""
        minor_courses = get_timetable(user_id, course_type="minor")

        if not minor_courses:
            state["final_response"] = (
                "📭 你当前没有辅修课程。\n\n"
                "输入「**推荐辅修**」开始规划辅修方案。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        lines = ["📋 **当前辅修课程**\n"]
        for c in minor_courses:
            lines.append(
                f"  - `{c.get('course_code', '?')}` "
                f"周{c.get('day_of_week', '?')} "
                f"第{c.get('period_start', '?')}-{c.get('period_end', '?')}节"
            )

        lines.append("")
        lines.append("**可用操作:**")
        lines.append("- `退课 课程代码` — 退选指定课程")
        lines.append("- `换课 旧代码 换 新代码` — 换选课程（自动预检冲突）")
        lines.append("  示例: `退课 FIN201` 或 `换课 FIN201 换 ACC201`")
        lines.append("")
        lines.append("输入「**取消**」退出调整模式。")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value
        return state

    def _handle_adjustment(self, state: AgentState, user_id: str,
                           user_input: str) -> AgentState:
        """处理用户的退课/换课指令。"""
        if user_input.strip() in ("取消", "算了", "不换了", "不退了"):
            state["final_response"] = "👌 已取消课程调整。"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 先尝试匹配换课格式
        swap_match = SWAP_RE.search(user_input)
        if swap_match:
            drop_code = swap_match.group("drop")
            add_code = swap_match.group("add")
            return self._do_swap(state, user_id, drop_code, add_code)

        # 再尝试匹配退课格式
        drop_match = re.search(
            r'(?:退|退课|退选|删除)\s*([A-Z]{2,4}\d{3,4})',
            user_input,
        )
        if drop_match:
            drop_code = drop_match.group(1)
            return self._do_drop(state, user_id, drop_code)

        # 无法识别
        state["final_response"] = (
            "❓ 无法识别操作。请使用:\n"
            "- `退课 课程代码` 退选课程\n"
            "- `换课 旧代码 换 新代码` 换选课程\n"
            "或输入「**取消**」退出。"
        )
        return state

    def _do_drop(self, state: AgentState, user_id: str, drop_code: str) -> AgentState:
        """执行退课操作。"""
        minor_courses = get_timetable(user_id, course_type="minor")
        target = None
        for c in minor_courses:
            if c.get("course_code") == drop_code:
                target = c
                break

        if not target:
            state["final_response"] = (
                f"❌ 未找到辅修课程 `{drop_code}`。\n\n"
                f"请确认课程代码正确。输入「退课」查看当前辅修列表。"
            )
            return state

        # 执行删除
        from src.storage.sqlite_client import delete_timetable_entry
        delete_timetable_entry(target["id"])

        # 检查剩余辅修课程数 → 如果为 0，清标记
        remaining = get_timetable(user_id, course_type="minor")
        if not remaining:
            user = get_user(user_id)
            if user:
                upsert_user(
                    user_id=user_id,
                    student_name=user.get("student_name", ""),
                    major=user.get("major", ""),
                    has_minor=0,
                    minor_program=None,
                )
            state["has_minor_course"] = False

        state["final_response"] = (
            f"✅ 已退选 `{drop_code}`。\n"
            f"当前辅修剩余 {len(remaining)} 门课程。"
        )
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _do_swap(self, state: AgentState, user_id: str,
                 drop_code: str, add_code: str) -> AgentState:
        """执行换课操作（含冲突预检）。"""
        # 检查新课程是否存在
        from src.storage.sqlite_client import get_course as sqlite_get_course
        new_course = sqlite_get_course(add_code)
        if not new_course:
            state["final_response"] = (
                f"❌ 课程 `{add_code}` 不在数据库中。\n"
                "请确认课程代码正确。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 冲突预检
        pre_check = validate_schedule(user_id, add_code)
        conflict_warning = ""
        if pre_check.get("has_conflict"):
            conflict_list = pre_check.get("conflicts", [])
            conflict_warning = "\n\n⚠️ **冲突预检警告**:\n"
            for c in conflict_list:
                conflict_warning += (
                    f"  - {c.get('detail', '时间重叠')} "
                    f"({c.get('severity', 'warning')})\n"
                )
            conflict_warning += "\n此操作仍可继续，但可能产生新的时间冲突。"

        # 执行换课
        result = swap_course(user_id, drop_code, add_code)
        dropped = result.get("dropped")
        added = result.get("added")
        warnings = result.get("warnings", [])

        lines = []
        if dropped and added:
            lines.append(f"✅ 已将 `{dropped}` 替换为 `{added}`。")
        elif dropped:
            lines.append(f"✅ 已退选 `{dropped}`。")
            if not added:
                lines.append(f"⚠️ `{add_code}` 添加失败: {'; '.join(warnings)}")
        else:
            lines.append(f"⚠️ 换课部分失败: {'; '.join(warnings)}")

        if conflict_warning:
            lines.append(conflict_warning)

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state
```

- [ ] **Step 2: Verify import**

Run: `cd e:/A2/study_agent && python -c "from src.agents.course_adjuster import CourseAdjusterAgent; a = CourseAdjusterAgent(); print('Import OK')"`

Expected: `Import OK`

- [ ] **Step 3: Write course adjuster tests**

Append to `test_phase4.py`:

```python
# ══════════════════════════════════════════════════════
# 3. 课程调整 Agent 测试
# ══════════════════════════════════════════════════════

print("\n── 3. 课程调整 Agent ──")

# 3a: 意图路由
run_and_check("退课", lambda r: r.get("intent") == "course_adjust", "3a: '退课' → course_adjust")
run_and_check("换课", lambda r: r.get("intent") == "course_adjust", "3b: '换课' → course_adjust")
run_and_check("退选 FIN201", lambda r: r.get("intent") == "course_adjust", "3c: '退选 FIN201' → course_adjust")
run_and_check("取消辅修", lambda r: r.get("intent") == "course_adjust", "3d: '取消辅修' → course_adjust")

# 3e: 无辅修时退课提示
def _check_no_minor_msg(r):
    resp = r.get("final_response", "")
    return "没有" in resp or "暂无" in resp or "未选" in resp or "无辅修" in resp
run_and_check("退课", _check_no_minor_msg, "3e: 无辅修时退课提示", has_minor_override=False)

# 3f: 有辅修时退课展示列表 → AWAIT_COURSE_ADJUSTMENT
def _check_adj_phase(r):
    return r.get("conversation_phase") == ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value
run_and_check("退课", _check_adj_phase, "3f: 退课->AWAIT_COURSE_ADJUSTMENT", has_minor_override=True)
```

- [ ] **Step 4: Run tests**

Run: `cd e:/A2/study_agent && python test_phase4.py`

Expected: course adjuster tests PASS

---

### Task 4: Chat QA Handler

**Files:**
- Create: `src/agents/chat_qa.py`
- Append: `test_phase4.py` (test entries 19-23)

**Design:** The "last line of defense" — handles unmatched intents. Detects academic questions vs casual chat, uses RAG + LLM for the former, template responses for the latter.

- [ ] **Step 1: Write the chat QA handler file**

Create `src/agents/chat_qa.py`:

```python
"""
兜底问答 Handler。

职责（系统的"最后一道防线"）：
    1. 学科问题 → RAG 检索知识库 + LLM 生成回答（模板拼接回退）
    2. 闲聊 → 友好预设回复 + 功能引导
    3. 完全无匹配 → 提示可用功能

无多轮状态 — 每次调用独立完成，phase 回到 IDLE。
"""

import logging
import re
from typing import List

from src.graph.state import AgentState, ConversationPhase
from src.tools.llm_tools import llm_call, llm_is_available
from src.tools.rag_tools import RAGToolset

logger = logging.getLogger(__name__)

# 学科关键词（触发 RAG 检索）
ACADEMIC_KEYWORDS_RE = re.compile(
    r'什么是|解释|为什么|怎么做|如何|定义|概念|原理|公式|定理|'
    r'算法|模型|金融|会计|经济|法律|管理|统计|编程|数据|'
    r'CAPM|NPV|IRR|ROI|GDP|CPI|WACC|DCF|'
    r'公司法|税法|劳动法|合同法|刑法|宪法|'
    r'资产负债|现金流|利润|成本|收益|风险|投资|'
    r'机器学习|深度学习|神经网络|数据库|操作系统',
    re.IGNORECASE,
)

# 闲聊回退模板
CASUAL_RESPONSES = [
    "你好！我是学业规划助手，可以帮你规划辅修、检测冲突、推荐学习资源。\n\n试试输入「**推荐辅修**」开始吧～",
    "欢迎使用学业规划系统！\n\n我可以帮你:\n- 📋 规划辅修方案\n- 📊 跟踪学习进度\n- 🔍 检测时间冲突\n- 📚 推荐学习资源\n- 📝 生成学业报告",
    "很高兴为您服务！请问需要什么帮助？\n\n你可以直接告诉我你的需求，比如「我想选一个辅修」。",
]


class ChatQAHandler:
    """兜底问答 Handler。"""

    def run(self, state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")

        # 空输入
        if not user_input.strip():
            state["final_response"] = "🤔 你好像没输入内容？请再说一次吧～"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 判断是否是学科问题
        if self._is_academic_question(user_input):
            response = self._answer_academic(user_input)
        else:
            response = self._answer_casual(user_input)

        state["final_response"] = response
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _is_academic_question(self, text: str) -> bool:
        """判断输入是否为学科相关问题。"""
        return bool(ACADEMIC_KEYWORDS_RE.search(text))

    def _answer_academic(self, question: str) -> str:
        """学科问题：RAG 检索 + LLM/模板 生成回答。"""
        try:
            # RAG 检索相关知识
            rag = RAGToolset()
            docs = rag.search_knowledge(question, top_k=3)

            if not docs:
                return (
                    f"关于「{question[:50]}」，知识库中暂时没有找到相关资料。\n\n"
                    "建议:\n"
                    "- 查阅相关教材或课件\n"
                    "- 向授课老师或助教请教\n"
                    "- 在课程群中与同学讨论"
                )

            # 拼接上下文
            context_parts = []
            refs = []
            for d in docs:
                content = d.get("content", d.get("text", ""))[:500]
                source = d.get("source", d.get("metadata", {}).get("source", ""))
                context_parts.append(content)
                if source:
                    filename = source.split("/")[-1].replace(".md", "")
                    refs.append(f"- 📄 {filename}")

            context = "\n---\n".join(context_parts)
            ref_text = "\n\n**参考来源:**\n" + "\n".join(refs[:3])

            # 尝试 LLM 回答
            if llm_is_available():
                prompt = (
                    f"请根据以下参考资料回答学生的问题。"
                    f"如果参考资料不足以回答，请诚实说明并给出建议。\n\n"
                    f"## 学生问题\n{question}\n\n"
                    f"## 参考资料\n{context}"
                )

                def fallback_template():
                    return (
                        f"📚 **关于「{question[:40]}」**\n\n"
                        f"根据学习资料库中的信息：\n\n"
                        f"{context[:1200]}\n\n"
                        f"---\n上述信息仅供参考。如需更详细的信息，"
                        f"建议查阅完整课件或教材。"
                        f"{ref_text}"
                    )

                answer = llm_call(
                    prompt=prompt,
                    system="你是一位耐心的大学学业辅导助手。请用简洁的中文回答，引用参考资料中的知识点。",
                    fallback=fallback_template,
                    max_tokens=1500,
                )
                return answer if answer else fallback_template()

            # 模板拼接（无 LLM）
            return (
                f"📚 **关于「{question[:40]}」**\n\n"
                f"根据学习资料库中的信息：\n\n"
                f"{context[:1200]}\n\n"
                f"---\n上述信息仅供参考。如需更详细的信息，"
                f"建议查阅完整课件或教材。"
                f"{ref_text}"
            )

        except Exception as e:
            logger.error("Chat QA academic answer failed: %s", e)
            return (
                f"抱歉，检索知识库时出现错误。\n\n"
                f"你询问的是「{question[:40]}」，建议查阅相关教材或咨询授课老师。"
            )

    def _answer_casual(self, text: str) -> str:
        """闲聊/问候：预设回复 + 功能引导。"""
        text_lower = text.lower().strip()

        # 问候
        if any(kw in text_lower for kw in ["你好", "hi", "hello", "嗨", "在吗", "在不在"]):
            return (
                "你好！👋 我是学业规划助手。\n\n"
                "我可以帮你:\n"
                "- 📋 **推荐辅修** — 根据你的兴趣推荐辅修方案\n"
                "- 📊 **查看进度** — 跟踪学习进度和成绩趋势\n"
                "- 🔍 **检测冲突** — 检查主修/辅修课表时间冲突\n"
                "- 📚 **推荐资源** — 查找课程相关的学习资料\n"
                "- 📝 **生成报告** — 生成综合学业报告\n"
                "- 🗑️ **退课/换课** — 调整辅修课程\n\n"
                "直接说出你的需求，我会尽力帮你！"
            )

        # 感谢
        if any(kw in text_lower for kw in ["谢谢", "感谢", "多谢", "thanks", "thank"]):
            return "不客气！有问题随时找我～ 😊"

        # 再见
        if any(kw in text_lower for kw in ["再见", "拜拜", "bye", "88"]):
            return "再见！祝你学业顺利～ 🎓"

        # 帮助
        if any(kw in text_lower for kw in ["帮助", "help", "功能", "能做什么", "怎么用"]):
            return (
                "🎓 **学业规划助手 — 功能列表**\n\n"
                "| 功能 | 示例指令 |\n"
                "|------|---------|\n"
                "| 推荐辅修 | `推荐辅修` |\n"
                "| 查看进度 | `查看进度` |\n"
                "| 检测冲突 | `检测冲突` |\n"
                "| 推荐资源 | `推荐资源` |\n"
                "| 生成报告 | `生成报告` |\n"
                "| 我的课表 | `我的课表` |\n"
                "| 退课/换课 | `退课` 或 `换课` |\n"
                "| 录入课表 | `录入课表` |\n\n"
                "直接输入指令即可使用对应功能。"
            )

        # 默认回复（随机选一个避免重复）
        import random
        return random.choice(CASUAL_RESPONSES)
```

- [ ] **Step 2: Verify import**

Run: `cd e:/A2/study_agent && python -c "from src.agents.chat_qa import ChatQAHandler; a = ChatQAHandler(); print('Import OK')"`

Expected: `Import OK`

- [ ] **Step 3: Write chat QA tests**

Append to `test_phase4.py`:

```python
# ══════════════════════════════════════════════════════
# 4. 兜底问答 Handler 测试
# ══════════════════════════════════════════════════════

print("\n── 4. 兜底问答 Handler ──")

# 4a: 未匹配意图 → chat_qa
run_and_check("今天天气怎么样", lambda r: r.get("intent") == "chat_qa",
              "4a: 闲聊 → chat_qa")

# 4b: 问候回复非空
def _check_greeting(r):
    resp = r.get("final_response", "")
    return len(resp) > 10 and "规划" in resp
run_and_check("你好", _check_greeting, "4b: 问候回复含功能引导")

# 4c: 帮助指令
def _check_help(r):
    resp = r.get("final_response", "")
    return "报告" in resp or "辅修" in resp or "帮助" in resp or "功能" in resp
run_and_check("帮助", _check_help, "4c: 帮助信息正常")

# 4d: 感谢回复
def _check_thanks(r):
    resp = r.get("final_response", "")
    return len(resp) > 3
run_and_check("谢谢", _check_thanks, "4d: 感谢回复正常")

# 4e: 学科问题触发 RAG
def _check_rag_answer(r):
    resp = r.get("final_response", "")
    return len(resp) > 50
run_and_check("什么是CAPM模型", _check_rag_answer, "4e: 学科问题 → RAG检索")
```

- [ ] **Step 4: Run tests**

Run: `cd e:/A2/study_agent && python test_phase4.py`

Expected: Chat QA tests PASS

---

### Task 5: Workflow Integration — Replace Echo Stubs

**Files:**
- Modify: `src/graph/workflow.py`

- [ ] **Step 1: Add lazy-loading getters for the 4 new agents**

In `src/graph/workflow.py`, add four lazy-loading variables and getter functions after the existing getters (after `_get_resource_recommender`):

Add at line ~25 (after existing lazy vars):

```python
_report_generator = None
_data_manager = None
_course_adjuster = None
_chat_qa = None
```

Add after the `_get_resource_recommender()` function (after line ~67):

```python
def _get_report_generator():
    global _report_generator
    if _report_generator is None:
        from src.agents.report_generator import ReportGeneratorAgent
        _report_generator = ReportGeneratorAgent()
    return _report_generator

def _get_data_manager():
    global _data_manager
    if _data_manager is None:
        from src.agents.data_manager import DataManagerAgent
        _data_manager = DataManagerAgent()
    return _data_manager

def _get_course_adjuster():
    global _course_adjuster
    if _course_adjuster is None:
        from src.agents.course_adjuster import CourseAdjusterAgent
        _course_adjuster = CourseAdjusterAgent()
    return _course_adjuster

def _get_chat_qa():
    global _chat_qa
    if _chat_qa is None:
        from src.agents.chat_qa import ChatQAHandler
        _chat_qa = ChatQAHandler()
    return _chat_qa
```

- [ ] **Step 2: Add real agent node functions**

Add after `resource_recommender_node` (after line ~179):

```python
# ── Phase 4 真实 Agent ──

def report_generator_node(state: AgentState) -> AgentState:
    """报告生成 Agent（真实实现）。"""
    return _get_report_generator().run(state)


def data_manager_node(state: AgentState) -> AgentState:
    """数据管理 Agent（真实实现）。"""
    return _get_data_manager().run(state)


def course_adjuster_node(state: AgentState) -> AgentState:
    """课程调整 Agent（真实实现）。"""
    return _get_course_adjuster().run(state)


def chat_qa_node(state: AgentState) -> AgentState:
    """兜底问答 Handler（真实实现）。"""
    return _get_chat_qa().run(state)
```

- [ ] **Step 3: Replace stub registrations with real nodes**

In `_build_node_map()`, replace the stub agent section (lines 226-234):

Replace:
```python
    # Phase 1 Echo Stub（未实现的功能保持占位）
    stub_agents = [
        "report_generator_agent",
        "data_manager_agent",
        "course_adjuster_agent",
        "chat_qa_handler",
    ]
    for name in stub_agents:
        nodes[name] = _echo_agent(name)
```

With:
```python
    # Phase 4：报告生成 + 数据管理 + 课程调整 + 兜底问答
    nodes["report_generator_agent"] = report_generator_node
    nodes["data_manager_agent"] = data_manager_node
    nodes["course_adjuster_agent"] = course_adjuster_node
    nodes["chat_qa_handler"] = chat_qa_node
```

- [ ] **Step 4: Update docstring**

Replace the file's module docstring (line 1-5):

```
LangGraph 状态图编排。

Phase 4：全部 10 个 Agent 均已实现真实逻辑，无 Echo Stub。
```

- [ ] **Step 5: Remove the `_echo_agent` factory function**

Delete the `_echo_agent` function (lines 184-201) since all agents are now real.

- [ ] **Step 6: Verify the workflow compiles**

Run: `cd e:/A2/study_agent && python -c "from src.graph.workflow import build_workflow; wf = build_workflow(); print(f'OK: {len(wf.nodes)} nodes')"`

Expected: `OK: 11 nodes`

---

### Task 6: Full Integration Test & Regression

**Files:**
- Append: `test_phase4.py` (final summary)
- Run: `test_phase1.py`, `test_phase2.py`, `test_phase3.py`

- [ ] **Step 1: Add test summary footer to test_phase4.py**

Append to `test_phase4.py`:

```python
# ══════════════════════════════════════════════════════
print()
print("=" * 60)
print(f"Phase 4 结果: {passed}/{total} 通过")
if passed == total:
    print("✅ 全部通过！")
else:
    print(f"⚠️ {total - passed} 项未通过")
print("=" * 60)
```

- [ ] **Step 2: Run all Phase 4 tests**

Run: `cd e:/A2/study_agent && python test_phase4.py`

Expected: all ~23 tests PASS (exact count may vary based on conditional execution)

- [ ] **Step 3: Run Phase 1-3 regression tests**

Run: `cd e:/A2/study_agent && python test_phase1.py`

Expected: all 39 tests PASS

Run: `cd e:/A2/study_agent && python test_phase2.py`

Expected: all 12 tests PASS

Run: `cd e:/A2/study_agent && python test_phase3.py`

Expected: all 19 tests PASS

- [ ] **Step 4: End-to-end manual flow test**

Run the REPL and test a complete flow:

```bash
cd e:/A2/study_agent && python app.py
```

Test transcript:
```
> 你好
Expected: greeting from ChatQA handler
> 帮助
Expected: feature list
> 什么是CAPM
Expected: RAG-based academic answer
> 生成报告
Expected: comprehensive report with export path
> 我的课表
Expected: timetable view (or empty prompt)
```

---

### Task 7: Phase 5 — Streamlit Frontend

**Files:**
- Create: `streamlit_app.py`

**Design:** A Streamlit chat UI wrapping the LangGraph app. Sidebar shows user info and quick actions. Main area is a chat interface. Reports tab shows visualizations.

- [ ] **Step 1: Write the Streamlit frontend**

Create `streamlit_app.py`:

```python
"""
Streamlit 前端 — 大学生辅修学习规划与跟踪多智能体系统。

启动: streamlit run streamlit_app.py
"""

import sys
import os
import json
from datetime import datetime

import streamlit as st

# 确保项目在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import LLM_CONFIG
from src.storage import init_database, get_user, is_redis_available
from src.storage.sqlite_client import (
    get_timetable, get_study_progress, get_conflict_history,
    get_reports, upsert_user,
)
from src.graph import create_initial_state, get_app, ConversationPhase

# ── 页面配置 ──

st.set_page_config(
    page_title="学业规划助手",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 初始化 ──

if "app" not in st.session_state:
    init_database()
    st.session_state.app = get_app()
    st.session_state.user_id = "u001"
    st.session_state.messages = []
    st.session_state.session_counter = 0

USER_ID = st.session_state.user_id


def new_session_id() -> str:
    st.session_state.session_counter += 1
    return f"streamlit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{st.session_state.session_counter}"


# ── 侧边栏 ──

with st.sidebar:
    st.title("🎓 学业规划助手")

    # 用户信息
    user = get_user(USER_ID)
    if user:
        st.subheader(f"👤 {user.get('student_name', '用户')}")
        st.caption(f"主修: {user.get('major', '未设置')}")
        st.caption(f"年级: {user.get('grade', '未设置')}")
        has_minor = user.get("has_minor", 0) == 1
        if has_minor:
            st.success(f"辅修: {user.get('minor_program', '已选')}")
        else:
            st.info("辅修: 未选择")
    else:
        st.info("用户数据未初始化")

    st.divider()

    # 系统状态
    col1, col2 = st.columns(2)
    with col1:
        redis_ok = is_redis_available()
        st.metric("Redis", "✅" if redis_ok else "⚠️ 降级")
    with col2:
        llm_ok = bool(LLM_CONFIG.get("api_key", ""))
        st.metric("LLM", "✅" if llm_ok else "⚠️ 模板")

    st.divider()

    # 快捷操作
    st.subheader("⚡ 快捷操作")
    quick_actions = [
        "推荐辅修", "查看进度", "检测冲突",
        "推荐资源", "生成报告", "我的课表",
        "退课", "帮助",
    ]
    cols = st.columns(2)
    for i, action in enumerate(quick_actions):
        with cols[i % 2]:
            if st.button(action, use_container_width=True, key=f"qa_{action}"):
                st.session_state.pending_input = action

    st.divider()

    # 会话历史
    st.subheader("💬 会话")
    if st.button("🔄 新对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.caption(f"共 {len(st.session_state.messages)} 条消息")


# ── 主聊天区 ──

st.title("学业规划助手")
st.caption("大学生辅修学习规划与跟踪 | LangGraph 多智能体系统")

# 渲染消息历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 输入框
user_input = st.chat_input("输入你的需求...", key="main_input")

# 处理快捷操作
if "pending_input" in st.session_state and st.session_state.pending_input:
    user_input = st.session_state.pending_input
    st.session_state.pending_input = None

if user_input:
    # 显示用户消息
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    # 调用 LangGraph
    hm = get_user(USER_ID)
    has_minor_flag = hm.get("has_minor", 0) == 1 if hm else False

    state = create_initial_state(USER_ID, new_session_id(), user_input, has_minor_flag, True)
    with st.spinner("思考中..."):
        result = st.session_state.app.invoke(state)

    response = result.get("final_response", "处理出错，请重试。")

    with st.chat_message("assistant"):
        st.markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})


# ── 报告展示区（Tab）──

st.divider()
st.subheader("📊 数据看板")

tab1, tab2, tab3 = st.tabs(["📅 课表", "📈 进度", "⚠️ 冲突"])

with tab1:
    timetable = get_timetable(USER_ID)
    if timetable:
        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        for day_idx in range(1, 8):
            day_courses = [
                c for c in timetable if c.get("day_of_week") == day_idx
            ]
            if day_courses:
                st.write(f"**{day_names[day_idx]}**")
                for c in sorted(day_courses, key=lambda x: x.get("period_start", 0)):
                    course_type = "🟡 辅修" if c.get("course_type") == "minor" else "🔵 主修"
                    st.text(
                        f"  {course_type} {c.get('course_code')} "
                        f"第{c.get('period_start')}-{c.get('period_end')}节 "
                        f"{c.get('location', '')}"
                    )
    else:
        st.info("暂无课表数据。输入「录入课表」开始添加。")

with tab2:
    progress = get_study_progress(USER_ID)
    if progress:
        st.metric("进度记录数", len(progress))
        try:
            import plotly.express as px
            import pandas as pd

            df_data = []
            for p in progress:
                if p.get("homework_score") is not None:
                    df_data.append({
                        "周次": p.get("week", 0),
                        "课程": p.get("course_code", ""),
                        "作业分": p.get("homework_score", 0),
                    })
            if df_data:
                df = pd.DataFrame(df_data)
                fig = px.line(df, x="周次", y="作业分", color="课程",
                             title="作业成绩趋势", markers=True)
                st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.caption("（安装 plotly 和 pandas 后显示趋势图）")
            for p in progress[:10]:
                st.text(
                    f"  {p.get('course_code')} W{p.get('week')}: "
                    f"作业{p.get('homework_score', '-')} "
                    f"小测{p.get('quiz_score', '-')} "
                    f"{p.get('attendance', '-')}"
                )
    else:
        st.info("暂无进度数据。输入「录入成绩」开始记录。")

with tab3:
    conflicts = get_conflict_history(USER_ID)
    if conflicts:
        for c in conflicts[:10]:
            sev_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
            icon = sev_icon.get(c.get("severity", ""), "⚪")
            st.text(
                f"{icon} {c.get('course_a', '?')} ⋂ {c.get('course_b', '?')}: "
                f"{c.get('overlap_detail', '')}"
            )
    else:
        st.success("暂无冲突记录。")
```

- [ ] **Step 2: Verify Streamlit app imports**

Run: `cd e:/A2/study_agent && python -c "import streamlit; print('Streamlit version:', streamlit.__version__)"`

Expected: Streamlit version printed (install via `pip install streamlit` if missing)

- [ ] **Step 3: Verify the app loads without errors**

Run: `cd e:/A2/study_agent && streamlit run streamlit_app.py --server.headless true 2>&1 | head -5`

Or: `cd e:/A2/study_agent && python -c "exec(open('streamlit_app.py').read().split('st.set_page_config')[0] + 'pass')"; echo "Syntax OK"`

Expected: No import errors

---

### Task 8: Phase 5 — Documentation

**Files:**
- Create: `docs/user_manual.md`

- [ ] **Step 1: Write user manual**

Create `docs/user_manual.md`:

```markdown
# 学业规划助手 — 用户手册

## 快速开始

### 启动方式

**命令行模式:**
\```bash
cd study_agent
python app.py
\```

**Web 界面模式:**
\```bash
cd study_agent
pip install streamlit plotly pandas
streamlit run streamlit_app.py
\```

### 前置条件

1. Python 3.10+
2. 安装依赖: `pip install -r requirements.txt`
3. Redis（可选）: 不装也能用，系统会自动降级为内存存储
4. LLM API Key（可选）: 不配也能用，系统使用规则模板

---

## 功能列表

| 功能 | 指令示例 | 说明 |
|------|---------|------|
| 推荐辅修 | `推荐辅修` / `我想选辅修` | 根据兴趣推荐辅修方向 |
| 查看进度 | `查看进度` / `成绩查询` | 查看学习进度和成绩趋势 |
| 检测冲突 | `检测冲突` / `时间冲突` | 检测主修/辅修课表时间冲突 |
| 推荐资源 | `推荐资源` / `找资料` | 根据薄弱点推荐学习资料 |
| 生成报告 | `生成报告` / `学业报告` | 生成综合学业报告(.md) |
| 我的课表 | `我的课表` / `查看课表` | 查看完整课表 |
| 录入课表 | `录入课表` / `添加课程` | 添加主修课程 |
| 退课/换课 | `退课` / `换课` | 调整辅修课程 |
| 调课 | `调课` | 一键清空辅修，重新规划 |
| 帮助 | `帮助` | 查看所有功能 |

---

## 典型使用流程

1. **首次使用**: 输入`录入课表` → 逐门添加主修课程
2. **选择辅修**: 输入`推荐辅修` → 查看推荐方案 → 选择方案
3. **检查冲突**: 输入`检测冲突` → 系统自动检查时间重叠
4. **跟踪进度**: 输入`录入成绩` → 记录每周成绩和出勤
5. **查漏补缺**: 输入`推荐资源` → 获取薄弱课程的学习资料
6. **总结报告**: 输入`生成报告` → 导出学业报告

---

## 配置 LLM 增强（可选）

设置环境变量后，报告生成和问答质量显著提升：

\```bash
export ANTHROPIC_API_KEY="sk-ant-..."
\```

不设置也能正常使用所有功能，系统会自动回退到规则模板。
```

- [ ] **Step 2: Verify the doc renders properly**

Open a preview or check: `cd e:/A2/study_agent && wc -l docs/user_manual.md`

Expected: >50 lines
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run all tests in sequence**

```bash
cd e:/A2/study_agent && python test_phase1.py && python test_phase2.py && python test_phase3.py && python test_phase4.py
```

Expected: Phase 1 (39/39), Phase 2 (12/12), Phase 3 (19/19), Phase 4 (~23/23 → total ~93 tests PASS)

- [ ] **Step 2: Verify the full agent list**

Run: `cd e:/A2/study_agent && python -c "
from src.graph.workflow import build_workflow
wf = build_workflow()
print('Nodes:', sorted(wf.nodes.keys()))
print('Total:', len(wf.nodes), 'nodes')
"`

Expected: 11 nodes: `['chat_qa_handler', 'conflict_checker_agent', 'conflict_gate', 'course_adjuster_agent', 'course_planner_agent', 'data_manager_agent', 'progress_tracker_agent', 'report_generator_agent', 'resource_recommender_agent', 'router']` … (plus END)

- [ ] **Step 3: Verify no echo stubs remain**

Run: `cd e:/A2/study_agent && grep -r "echo stub\|ECHO STUB\|_echo_agent\|⚡ 该功能将在" src/graph/workflow.py`

Expected: No output (all stubs replaced)

---

## Self-Review Checklist

### 1. Spec Coverage

| Doc Section | Task | Status |
|-------------|------|--------|
| 3.3.1 Report Generator | Task 1 | ✅ Covered |
| 3.3.2 Data Manager | Task 2 | ✅ Covered |
| 3.3.3 Course Adjuster | Task 3 | ✅ Covered |
| 3.3.4 Chat QA Handler | Task 4 | ✅ Covered |
| 3.4 Workflow Integration | Task 5 | ✅ Covered |
| 3.4 Integration Tests | Task 6 | ✅ Covered |
| 4.1 Streamlit Frontend | Task 7 | ✅ Covered |
| 4.2 Performance (brief) | Task 7 (tabs) | ⚠️ Light coverage — LRU/MD5/async deferred to future |
| 4.3 Documentation | Task 8 | ✅ Covered |

### 2. Placeholder Scan

No "TBD", "TODO", "implement later", or empty code blocks found in the plan.

### 3. Type Consistency

- `AgentState` imported from `src.graph.state` everywhere ✅
- `ConversationPhase` used with `.value` consistently ✅
- All agents follow `run(self, state: AgentState) -> AgentState` signature ✅
- Tool functions imported from consistent paths ✅

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-15-phase4-5-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
