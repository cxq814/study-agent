#!/usr/bin/env python
"""Phase 2 集成测试 — RAG 索引 + 课程规划 + 进度跟踪。"""

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

init_database()

USER_ID = "u001"
SESSION_ID = "phase2_test"

user = get_user(USER_ID)
has_minor = user.get("has_minor", 0) == 1 if user else False
app = get_app()

print("=" * 60)
print("Phase 2 集成测试")
print(f"用户: {user['student_name'] if user else 'N/A'}")
print(f"辅修: {'已选' if has_minor else '未选'}")
print(f"Redis: {'已连接' if is_redis_available() else '未连接'}")
print("=" * 60)

total = passed = 0


def run_and_check(user_input: str, check_fn, desc: str) -> bool:
    """运行一次完整流程并用 check_fn 验证结果。"""
    global total, passed
    total += 1
    state = create_initial_state(USER_ID, SESSION_ID, user_input, has_minor, True)
    result = app.invoke(state)
    ok = check_fn(result)
    passed += ok
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {desc}")
    return ok


# ═══════════════════════════════════════════════
# 测试 1：RAG 索引构建
# ═══════════════════════════════════════════════
print("\n── 1. RAG 索引构建 ──")
total += 1
try:
    from src.rag.indexer import build_index
    collection = build_index(force_rebuild=True)
    chunk_count = collection.count()
    ok = chunk_count > 0
    passed += ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 知识库索引: {chunk_count} 个 chunks")
except Exception as e:
    print(f"  [FAIL] RAG 索引构建失败: {e}")

# ═══════════════════════════════════════════════
# 测试 2：RAG 检索
# ═══════════════════════════════════════════════
print("\n── 2. RAG 检索 ──")
total += 1
try:
    from src.rag.retriever import Retriever
    retriever = Retriever(collection=collection)
    results = retriever.search("金融辅修课程推荐", top_k=3)
    ok = len(results) > 0
    passed += ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 检索 '金融辅修' → {len(results)} 条结果")
    for r in results[:2]:
        src = r.get("metadata", {}).get("source", "?")
        dist = r.get("distance", 0)
        print(f"    source={src}  dist={dist:.3f}")
except Exception as e:
    print(f"  [FAIL] RAG 检索失败: {e}")

# ═══════════════════════════════════════════════
# 测试 3：课程规划 — 新推荐
# ═══════════════════════════════════════════════
print("\n── 3. 课程规划 Agent（推荐）──")
run_and_check(
    "推荐辅修",
    lambda r: "辅修课程推荐方案" in r.get("final_response", "")
              and "方案" in r.get("final_response", ""),
    "生成辅修推荐清单"
)

# 验证 phase 切换
state = create_initial_state(USER_ID, "check_phase", "推荐辅修", False, True)
result = app.invoke(state)
phase_check = result.get("conversation_phase") == ConversationPhase.AWAIT_COURSE_SELECTION.value
total += 1; passed += phase_check
print(f"  [{'PASS' if phase_check else 'FAIL'}] Phase → AWAIT_COURSE_SELECTION")

# 验证 Redis/state 中有暂存的推荐
recs = result.get("current_recommendations", [])
total += 1; passed += len(recs) > 0
print(f"  [{'PASS' if len(recs) > 0 else 'FAIL'}] 推荐结果暂存: {len(recs)} 个方案")

# ═══════════════════════════════════════════════
# 测试 4：课程规划 — 方案选择
# ═══════════════════════════════════════════════
print("\n── 4. 课程规划 Agent（选择确认）──")

# 先触发推荐，暂存结果
s1 = create_initial_state(USER_ID, "select_test", "推荐辅修", False, True)
r1 = app.invoke(s1)
# 模拟 phase 保持
save_session_state(USER_ID, "select_test", {
    "conversation_phase": ConversationPhase.AWAIT_COURSE_SELECTION.value,
    "has_minor_course": False,
    "current_recommendations": r1.get("current_recommendations", []),
})

# 选择方案一
s2 = create_initial_state(USER_ID, "select_test", "选方案一", False, True)
redis_state = load_session_state(USER_ID, "select_test")
if redis_state:
    s2["conversation_phase"] = redis_state.get("conversation_phase", "idle")
    s2["current_recommendations"] = redis_state.get("current_recommendations", [])
else:
    s2["conversation_phase"] = ConversationPhase.AWAIT_COURSE_SELECTION.value
    s2["current_recommendations"] = r1.get("current_recommendations", [])

r2 = app.invoke(s2)
ok = "辅修选择确认成功" in r2.get("final_response", "")
total += 1; passed += ok
print(f"  [{'PASS' if ok else 'FAIL'}] 选择方案一 → 选课确认")
if not ok:
    print(f"    Response: {r2.get('final_response', '')[:150]}")

# 验证 has_minor 已更新
minor_check = r2.get("has_minor_course") == True
total += 1; passed += minor_check
print(f"  [{'PASS' if minor_check else 'FAIL'}] has_minor_course → True")

# ═══════════════════════════════════════════════
# 测试 5：进度跟踪 — 查询
# ═══════════════════════════════════════════════
print("\n── 5. 进度跟踪 Agent（查询）──")
run_and_check(
    "查看进度",
    lambda r: "学习进度" in r.get("final_response", ""),
    "查询学习进度"
)

run_and_check(
    "查看 CS201 进度",
    lambda r: "CS201" in r.get("final_response", "") or "学习进度" in r.get("final_response", ""),
    "查询指定课程进度"
)

# ═══════════════════════════════════════════════
# 测试 6：进度跟踪 — 录入
# ═══════════════════════════════════════════════
print("\n── 6. 进度跟踪 Agent（录入）──")
run_and_check(
    "录入成绩 FIN101 第8周 作业90 考勤正常 小测85",
    lambda r: "进度已录入" in r.get("final_response", ""),
    "录入完整进度信息"
)

# 验证数据写入
from src.storage import get_study_progress
records = get_study_progress(USER_ID, course_code="FIN101")
fin101_count = len([r for r in records if r.get("week") == 8])
total += 1; passed += fin101_count > 0
print(f"  [{'PASS' if fin101_count > 0 else 'FAIL'}] SQLite 验证: FIN101 第8周已写入")

# ═══════════════════════════════════════════════
# 测试 7：无辅修时冲突检测仍被拦截
# ═══════════════════════════════════════════════
print("\n── 7. 权限门回归（无辅修 → 拦截）──")
# 重置用户为无辅修状态
from src.storage import upsert_user
upsert_user(USER_ID, user["student_name"], user["major"],
            user.get("grade"), user.get("available_slots"),
            user.get("interests"), has_minor=0, minor_program=None)

run_and_check(
    "检测冲突",
    lambda r: "暂未选择辅修" in r.get("final_response", ""),
    "无辅修时冲突检测被正确拦截"
)

# ═══════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"Phase 2 测试结果: {passed}/{total} 通过")
if passed == total:
    print("✅ 全部通过！")
else:
    print(f"⚠️ {total - passed} 项失败")
print("=" * 60)
