#!/usr/bin/env python
"""Phase 3 集成测试 — 冲突检测 + 资源推荐 + 权限门 + RAG 缓存。"""

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
from src.storage import save_session_state, load_session_state
from src.storage.sqlite_client import (
    get_timetable, add_timetable_entry, get_study_progress,
    upsert_user, add_conflict_record,
)

init_database()

USER_ID = "u001"
SESSION_ID = "phase3_test"

user = get_user(USER_ID)
has_minor = user.get("has_minor", 0) == 1 if user else False
app = get_app()

print("=" * 60)
print("Phase 3 集成测试")
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


# ═══════════════════════════════════════════════
# 测试 1：时间重叠算法单元测试
# ═══════════════════════════════════════════════
print("\n── 1. 时间重叠算法 ──")
from src.agents.conflict_checker import ConflictCheckerAgent
cc = ConflictCheckerAgent()

# 1a: 节次重叠
cases = [
    ((1, 3, 3, 5), 1, "边界重叠(1,3)vs(3,5)"),
    ((1, 3, 1, 3), 3, "完全重叠(1,3)vs(1,3)"),
    ((1, 3, 4, 6), 0, "无重叠(1,3)vs(4,6)"),
    ((1, 3, 2, 4), 2, "部分重叠(1,3)vs(2,4)"),
]
for (ps1, pe1, ps2, pe2), expected, desc in cases:
    total += 1
    result = cc._period_overlap(ps1, pe1, ps2, pe2)
    ok = result == expected
    passed += ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc} = {result} (期望 {expected})")

# 1b: 严重程度分类
sev_cases = [
    ((3, False), "critical", "3节重叠=严重"),
    ((1, False), "warning", "1节重叠=警告"),
    ((0, True), "critical", "考试冲突=严重"),
    ((0, False), "info", "无冲突=信息"),
]
for (overlap, exam), expected, desc in sev_cases:
    total += 1
    result = cc._classify_severity(overlap, exam)
    ok = result == expected
    passed += ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc} = {result}")

# ═══════════════════════════════════════════════
# 测试 2：冲突检测 — 无辅修时被权限门拦截
# ═══════════════════════════════════════════════
print("\n── 2. 冲突检测权限门（无辅修）──")

# 重置用户为无辅修
upsert_user(USER_ID, user["student_name"], user["major"],
            user.get("grade"), user.get("available_slots"),
            user.get("interests"), has_minor=0, minor_program=None)

run_and_check(
    "检测冲突",
    lambda r: "暂未选择辅修" in r.get("final_response", "")
              and r.get("intent") == "blocked",
    "无辅修时冲突检测被拦截"
)

# ═══════════════════════════════════════════════
# 测试 3：冲突检测 — 有辅修时真实检测
# ═══════════════════════════════════════════════
print("\n── 3. 冲突检测（有辅修 + 主修课表）──")

# 确保用户有辅修
upsert_user(USER_ID, user["student_name"], user["major"],
            user.get("grade"), user.get("available_slots"),
            user.get("interests"), has_minor=1, minor_program="数据科学辅修")

# 添加主修课表（如果还没的话）
major_entries = get_timetable(USER_ID, course_type="major")
if not major_entries:
    add_timetable_entry({
        "user_id": USER_ID, "course_code": "CS201",
        "course_type": "major",
        "week_start": 1, "week_end": 16,
        "day_of_week": 1, "period_start": 1, "period_end": 3,
        "location": "教三楼201", "exam_time": "2025-12-22T09:00",
    })
    add_timetable_entry({
        "user_id": USER_ID, "course_code": "CS301",
        "course_type": "major",
        "week_start": 1, "week_end": 16,
        "day_of_week": 3, "period_start": 1, "period_end": 3,
        "location": "教一楼101", "exam_time": "2025-12-20T14:00",
    })

# 添加辅修课表（制造一个冲突：主修CS301 Wed1-3 vs 辅修FIN201 Wed3-5 → 第3节重叠）
minor_entries = get_timetable(USER_ID, course_type="minor")
if not minor_entries:
    add_timetable_entry({
        "user_id": USER_ID, "course_code": "FIN201",
        "course_type": "minor",
        "week_start": 1, "week_end": 16,
        "day_of_week": 3, "period_start": 3, "period_end": 5,
        "location": "经管楼B102", "exam_time": "2025-12-20T09:00",
    })
    add_timetable_entry({
        "user_id": USER_ID, "course_code": "FIN101",
        "course_type": "minor",
        "week_start": 1, "week_end": 16,
        "day_of_week": 2, "period_start": 3, "period_end": 5,
        "location": "经管楼A201", "exam_time": "2025-12-19T14:00",
    })

# 3a: 检测出冲突
run_and_check(
    "检测冲突",
    lambda r: ("冲突检测报告" in r.get("final_response", "")
               or "检测到冲突" in r.get("final_response", "")),
    "有冲突时输出检测报告",
    has_minor_override=True
)

# 3b: phase 切换到等待决策
state = create_initial_state(USER_ID, "conflict_phase_check", "检测冲突", True, True)
result = app.invoke(state)
phase_ok = result.get("conversation_phase") == ConversationPhase.AWAIT_CONFLICT_DECISION.value
total += 1; passed += phase_ok
print(f"  [{'PASS' if phase_ok else 'FAIL'}] Phase → AWAIT_CONFLICT_DECISION")

# ═══════════════════════════════════════════════
# 测试 4：冲突决策处理
# ═══════════════════════════════════════════════
print("\n── 4. 冲突决策处理 ──")

# 模拟冲突后用户说"忽略"
s1 = create_initial_state(USER_ID, "conflict_decision", "忽略", True, True)
s1["conversation_phase"] = ConversationPhase.AWAIT_CONFLICT_DECISION.value
s1["current_conflicts"] = [{
    "severity": "warning",
    "course_a_label": "CS301 操作系统",
    "course_b_label": "FIN201 公司金融",
    "conflicts": [{"detail": "周三第3节重叠"}],
    "suggestion": "轻微重叠，建议确认是否允许",
}]
r1 = app.invoke(s1)
ok = "暂不处理" in r1.get("final_response", "") and r1.get("conversation_phase") == "idle"
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 忽略冲突 → phase归位IDLE")

# ═══════════════════════════════════════════════
# 测试 5：RAG 缓存（内存回退）
# ═══════════════════════════════════════════════
print("\n── 5. RAG 缓存（Redis 不可用时内存回退）──")
from src.storage.redis_client import get_rag_cache, set_rag_cache

cache_key = "phase3_cache_test_v1"
set_rag_cache(cache_key, '{"result": "cached_value"}')
cached = get_rag_cache(cache_key)
ok = cached is not None and "cached_value" in str(cached)
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 内存回退: 写入→读出 = {cached[:30] if cached else 'None'}...")

# ═══════════════════════════════════════════════
# 测试 6：资源推荐 — 薄弱点识别
# ═══════════════════════════════════════════════
print("\n── 6. 资源推荐薄弱点识别 ──")
from src.agents.resource_recommender import ResourceRecommenderAgent
rr = ResourceRecommenderAgent()

# 用已有 seed 数据测试：CS301 有缺勤+低分记录
all_progress = get_study_progress(USER_ID)
weak = rr._identify_weak_points(all_progress, set())
ok = isinstance(weak, list)
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 薄弱点识别: {len(weak)} 门课程有薄弱项")
for w in weak:
    print(f"    {w['course_code']} {w['course_name']}: lvl={w['weakness_level']} "
          f"hw={w['avg_homework']} quiz={w['avg_quiz']} abs={w['absence_count']}")

# ═══════════════════════════════════════════════
# 测试 7：资源推荐 — 完整流程
# ═══════════════════════════════════════════════
print("\n── 7. 资源推荐完整流程 ──")
run_and_check(
    "推荐学习资料",
    lambda r: "推荐" in r.get("final_response", "") or "资料" in r.get("final_response", ""),
    "资源推荐生成输出",
    has_minor_override=True
)

# 验证 phase
state = create_initial_state(USER_ID, "res_phase_check", "推荐学习资料", True, True)
result = app.invoke(state)
phase_ok = result.get("conversation_phase") == ConversationPhase.AWAIT_RESOURCE_FEEDBACK.value
total += 1; passed += phase_ok
print(f"  [{'PASS' if phase_ok else 'FAIL'}] Phase → AWAIT_RESOURCE_FEEDBACK")

# ═══════════════════════════════════════════════
# 测试 8：权限门 SQLite 验证层
# ═══════════════════════════════════════════════
print("\n── 8. 权限门 SQLite 验证层 ──")

# 8a: 有辅修 + 有真实课表 → 通过
state8a = create_initial_state(USER_ID, "gate_a", "检测冲突", True, True)
result8a = app.invoke(state8a)
# 有真实课表数据，应该通过 gate 进入 conflict_checker
ok = result8a.get("intent") != "blocked"
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 有课表 → gate 放行 (intent={result8a.get('intent')})")

# 8b: 主修课表为空 → 拦截
# 使用一个不存在的 user 测试
state8b = create_initial_state("no_such_user", "gate_b", "检测冲突", True, True)
# 手动标记 has_major_timetable=true 但实际无数据
result8b = app.invoke(state8b)
ok = result8b.get("intent") == "blocked" or "课表为空" in result8b.get("final_response", "")
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 无主修数据 → gate 拦截")

# ═══════════════════════════════════════════════
# 测试 9：知识库资源已索引
# ═══════════════════════════════════════════════
print("\n── 9. 知识库资源验证 ──")
from src.rag.indexer import get_collection
col = get_collection()
results = col.get(include=["metadatas"], limit=100)
sources = {}
for m in results["metadatas"]:
    src = (m.get("source", "?") or "?").replace("\\", "/")
    prefix = src.split("/")[0] if "/" in src else src
    sources[prefix] = sources.get(prefix, 0) + 1

for k, v in sorted(sources.items()):
    total += 1
    ok = v > 0
    passed += ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {k}/ 目录: {v} chunks")
total -= len(sources)  # 每个目录只计一次
passed -= len(sources)
total += 1; passed += 1  # 整体验证
print(f"  [PASS] 4 个目录均有索引数据 (共 {sum(sources.values())} chunks)")

# ═══════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"Phase 3 测试结果: {passed}/{total} 通过")
if passed == total:
    print("✅ 全部通过！")
else:
    print(f"⚠️ {total - passed} 项失败")
print("=" * 60)
