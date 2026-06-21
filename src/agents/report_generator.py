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

from src.graph.state import AgentState, ConversationPhase
from src.tools.llm_tools import llm_call, llm_is_available
from src.tools.stats_tools import (
    calc_progress_trend,
    calc_conflict_summary,
    calc_risk_score,
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

            # 3. 填充模板
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

            # 4. 生成报告内容
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
                "conflict_total": conflict_summary.get("total", 0),
                "progress_courses": len(trends),
            }, ensure_ascii=False)
            add_report({
                "user_id": user_id,
                "report_type": "comprehensive",
                "content_md": report_content,
                "summary_json": summary_json,
            })

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
                f"**冲突**: {conflict_summary.get('total', 0)} 条记录\n"
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
