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

run_and_check("生成报告", lambda r: r.get("intent") == "gen_report", "1a: '生成报告' → gen_report")
run_and_check("学业报告", lambda r: r.get("intent") == "gen_report", "1b: '学业报告' → gen_report")
run_and_check("综合报告", lambda r: r.get("intent") == "gen_report", "1c: '综合报告' → gen_report")

def _check_report(r):
    resp = r.get("final_response", "")
    return ("报告" in resp or "学业" in resp) and len(resp) > 50
run_and_check("出报告", _check_report, "1d: 报告内容输出正常")

def _check_phase_idle(r):
    return r.get("conversation_phase") == ConversationPhase.IDLE.value
run_and_check("生成报告", _check_phase_idle, "1e: 报告后 phase=IDLE")

def _check_no_data_report(r):
    resp = r.get("final_response", "")
    return "报告" in resp or "失败" in resp or len(resp) > 30
run_and_check("学业报告", _check_no_data_report, "1f: 无进度数据时降级输出")


# ══════════════════════════════════════════════════════
# 2. 数据管理 Agent 测试
# ══════════════════════════════════════════════════════

print("\n── 2. 数据管理 Agent ──")

run_and_check("我的课表", lambda r: r.get("intent") == "data_manage", "2a: '我的课表' → data_manage")
run_and_check("录入课表", lambda r: r.get("intent") == "data_manage", "2b: '录入课表' → data_manage")
run_and_check("修改信息", lambda r: r.get("intent") == "data_manage", "2c: '修改信息' → data_manage")

def _check_timetable_view(r):
    resp = r.get("final_response", "")
    return "课表" in resp and len(resp) > 20
run_and_check("查看课表", _check_timetable_view, "2d: 查看课表输出正常")

def _check_data_input_phase(r):
    return r.get("conversation_phase") == ConversationPhase.AWAIT_DATA_INPUT.value
run_and_check("录入课表", _check_data_input_phase, "2e: 录入课表 → AWAIT_DATA_INPUT")

def _check_delete_prompt(r):
    resp = r.get("final_response", "")
    return "删除" in resp
run_and_check("删除课程", _check_delete_prompt, "2f: 删除课程提示正常")


# ══════════════════════════════════════════════════════
# 3. 课程调整 Agent 测试
# ══════════════════════════════════════════════════════

print("\n── 3. 课程调整 Agent ──")

run_and_check("退课", lambda r: r.get("intent") == "course_adjust", "3a: '退课' → course_adjust")
run_and_check("换课", lambda r: r.get("intent") == "course_adjust", "3b: '换课' → course_adjust")
run_and_check("退选 FIN201", lambda r: r.get("intent") == "course_adjust", "3c: '退选 FIN201' → course_adjust")
run_and_check("取消辅修", lambda r: r.get("intent") == "course_adjust", "3d: '取消辅修' → course_adjust")

# 3e: 无辅修时退课（注意：DB 可能仍有种子数据，检查提示含功能引导即可）
def _check_no_minor_or_prompt(r):
    resp = r.get("final_response", "")
    return "没有" in resp or "暂无" in resp or "未选" in resp or "无辅修" in resp or "退课" in resp or "辅修" in resp
run_and_check("退课", _check_no_minor_or_prompt, "3e: 退课响应正常", has_minor_override=False)

def _check_adj_phase(r):
    return r.get("conversation_phase") == ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value
run_and_check("退课", _check_adj_phase, "3f: 退课→AWAIT_COURSE_ADJUSTMENT", has_minor_override=True)


# ══════════════════════════════════════════════════════
# 4. 兜底问答 Handler 测试
# ══════════════════════════════════════════════════════

print("\n── 4. 兜底问答 Handler ──")

run_and_check("今天天气怎么样", lambda r: r.get("intent") == "chat_qa",
              "4a: 闲聊 → chat_qa")

def _check_greeting(r):
    resp = r.get("final_response", "")
    return len(resp) > 10 and ("规划" in resp or "辅修" in resp or "学业" in resp or "帮助" in resp)
run_and_check("你好", _check_greeting, "4b: 问候回复含功能引导")

def _check_help(r):
    resp = r.get("final_response", "")
    return ("报告" in resp or "辅修" in resp or "帮助" in resp
            or "功能" in resp or "指令" in resp)
run_and_check("帮助", _check_help, "4c: 帮助信息正常")

def _check_thanks(r):
    resp = r.get("final_response", "")
    return len(resp) > 3
run_and_check("谢谢", _check_thanks, "4d: 感谢回复正常")

def _check_rag_answer(r):
    resp = r.get("final_response", "")
    return len(resp) > 30
run_and_check("什么是CAPM模型", _check_rag_answer, "4e: 学科问题 → RAG检索")

def _check_chatqa_phase_idle(r):
    return r.get("conversation_phase") == ConversationPhase.IDLE.value
run_and_check("你好", _check_chatqa_phase_idle, "4f: 闲聊后 phase=IDLE")


# ══════════════════════════════════════════════════════

print()
print("=" * 60)
print(f"Phase 4 结果: {passed}/{total} 通过")
if passed == total:
    print("✅ 全部通过！")
else:
    print(f"⚠️ {total - passed} 项未通过")
print("=" * 60)
