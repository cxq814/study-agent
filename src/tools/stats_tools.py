"""
数据统计分析工具 — 聚合学习进度、冲突记录，计算趋势与风险分数。

调用方：报告生成 Agent。
"""

import logging
from typing import List, Dict
from collections import defaultdict

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  进度趋势
# ══════════════════════════════════════════════════════

def calc_progress_trend(progress: list) -> dict:
    """
    计算学习进度趋势。

    输入: get_study_progress() 返回的记录列表
    输出: {
        "FIN101": {
            "weeks": [1, 2, 3],
            "homework_scores": [88, 90, 92],
            "quiz_scores": [85, 88, 90],
            "attendance": ["present", "present", "absent"],
            "hw_trend": "up",      # 作业趋势: up/down/flat
            "quiz_trend": "up",    # 小测趋势
            "attendance_rate": 67  # 出勤率 %
        }
    }
    """
    if not isinstance(progress, list) or not progress:
        return {}

    try:
        by_course = defaultdict(list)
        for p in progress:
            code = p.get("course_code", "")
            if code:
                by_course[code].append(p)

        result = {}
        for code, records in by_course.items():
            records.sort(key=lambda r: r.get("week", 0))
            weeks = [r.get("week", 0) for r in records]
            hw = [r.get("homework_score") for r in records
                  if r.get("homework_score") is not None]
            quiz = [r.get("quiz_score") for r in records
                    if r.get("quiz_score") is not None]
            att = [r.get("attendance", "") for r in records]

            presence = sum(1 for a in att if a == "present")
            total = len(att)
            attendance_rate = round(presence / total * 100, 1) if total > 0 else 0

            result[code] = {
                "weeks": weeks,
                "homework_scores": hw,
                "quiz_scores": quiz,
                "attendance": att,
                "hw_trend": _calc_trend(hw),
                "quiz_trend": _calc_trend(quiz),
                "attendance_rate": attendance_rate,
            }

        return result
    except Exception as e:
        logger.error("calc_progress_trend failed: %s", e)
        return {}


def _calc_trend(scores: list) -> str:
    """简单线性趋势判断：比较首尾平均值。"""
    if len(scores) < 2:
        return "flat"
    first_half = scores[:len(scores)//2]
    second_half = scores[len(scores)//2:]
    avg1 = sum(first_half) / len(first_half)
    avg2 = sum(second_half) / len(second_half)
    diff = avg2 - avg1
    if diff > 2:
        return "up"
    elif diff < -2:
        return "down"
    return "flat"


# ══════════════════════════════════════════════════════
#  冲突汇总
# ══════════════════════════════════════════════════════

def calc_conflict_summary(conflicts: list) -> dict:
    """
    冲突汇总统计。

    输入: get_conflict_history() 返回的记录列表
    输出: {
        "total": 3,
        "critical_count": 1,
        "warning_count": 2,
        "info_count": 0,
        "affected_courses": ["CS301", "FIN201", "CS202"]
    }
    """
    if not isinstance(conflicts, list):
        return {"total": 0, "critical_count": 0, "warning_count": 0,
                "info_count": 0, "affected_courses": []}

    try:
        summary = {"total": len(conflicts), "critical_count": 0,
                    "warning_count": 0, "info_count": 0,
                    "affected_courses": []}
        courses = set()
        for c in conflicts:
            sev = c.get("severity", "info")
            if sev == "critical":
                summary["critical_count"] += 1
            elif sev == "warning":
                summary["warning_count"] += 1
            else:
                summary["info_count"] += 1
            if c.get("course_a"):
                courses.add(c["course_a"])
            if c.get("course_b"):
                courses.add(c["course_b"])
        summary["affected_courses"] = sorted(courses)
        return summary
    except Exception as e:
        logger.error("calc_conflict_summary failed: %s", e)
        return {"total": 0, "critical_count": 0, "warning_count": 0,
                "info_count": 0, "affected_courses": []}


# ══════════════════════════════════════════════════════
#  综合风险评分
# ══════════════════════════════════════════════════════

def calc_risk_score(progress: list, conflicts: list) -> float:
    """
    综合风险量化（0-100）。

    计分规则：
      - 每缺勤 1 次 → +3 分
      - 作业均分 < 70 → +2 分/门
      - 小测均分 < 60 → +2 分/门
      - 每个严重冲突 → +5 分
      - 每个轻微冲突 → +1 分
    上限 100 分。
    """
    if not isinstance(progress, list):
        progress = []
    if not isinstance(conflicts, list):
        conflicts = []

    try:
        score = 0.0

        # 进度维度
        by_course = defaultdict(list)
        for p in progress:
            code = p.get("course_code", "")
            if code:
                by_course[code].append(p)

        total_absent = 0
        for code, records in by_course.items():
            hw = [r.get("homework_score") for r in records
                  if r.get("homework_score") is not None]
            quiz = [r.get("quiz_score") for r in records
                    if r.get("quiz_score") is not None]
            absent = sum(1 for r in records if r.get("attendance") == "absent")
            total_absent += absent

            if hw:
                avg_hw = sum(hw) / len(hw)
                if avg_hw < 70:
                    score += 2
            if quiz:
                avg_quiz = sum(quiz) / len(quiz)
                if avg_quiz < 60:
                    score += 2

        score += total_absent * 3

        # 冲突维度
        for c in conflicts:
            sev = c.get("severity", "")
            if sev == "critical":
                score += 5
            elif sev == "warning":
                score += 1

        return round(min(score, 100), 1)
    except Exception as e:
        logger.error("calc_risk_score failed: %s", e)
        return 0.0


# ══════════════════════════════════════════════════════
#  群体对比（预留）
# ══════════════════════════════════════════════════════

def compare_with_peers(user_progress: dict, program: str) -> dict:
    """
    与同专业平均水平对比（预留接口）。

    当前版本返回课程级别的简单百分位估算，
    未来接入群体数据后提供真实对比。
    """
    if not isinstance(user_progress, dict):
        return {}

    try:
        result = {}
        for code, data in user_progress.items():
            hw_scores = data.get("homework_scores", [])
            avg = sum(hw_scores) / len(hw_scores) if hw_scores else 0
            # 简单估算：分数越高排名越靠前
            if avg >= 90:
                percentile = 80
            elif avg >= 80:
                percentile = 60
            elif avg >= 70:
                percentile = 40
            elif avg >= 60:
                percentile = 20
            else:
                percentile = 10

            result[code] = {
                "avg_score": round(avg, 1),
                "estimated_percentile": percentile,
                "note": "当前为估算值，基于分数区间映射，非真实群体数据",
            }
        return result
    except Exception as e:
        logger.error("compare_with_peers failed: %s", e)
        return {}
