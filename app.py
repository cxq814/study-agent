#!/usr/bin/env python
"""
大学生辅修学习规划与跟踪多智能体系统 — CLI 入口

用法:
    python app.py [--user-id u001]

Phase 1 功能:
    - 数据库初始化（SQLite 建表 + 种子数据）
    - Redis 会话状态管理（可选，无 Redis 时优雅降级）
    - 意图路由（关键词匹配 + LLM stub 兜底）
    - LangGraph 状态图运转（Echo Stub 验证）
"""

import sys
import os
import uuid
import logging
import argparse
from typing import Optional

# Windows 环境下强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

from config.settings import LOG_LEVEL
from src.storage import (
    init_database, get_user, is_redis_available,
    save_session_state, load_session_state,
)
from src.graph import create_initial_state, get_app, ConversationPhase

# ── 日志 ────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")

# ── 界面常量 ────────────────────────────────────────

BANNER = r"""
╔══════════════════════════════════════════════════════╗
║      📚 大学生辅修学习规划与跟踪多智能体系统          ║
║                  Phase 1 — 基础设施                    ║
╚══════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
📋 可用操作（直接输入即可）：
  ┌────────────────────────────────────────────────┐
  │ "推荐辅修" — 课程规划与辅修推荐                 │
  │ "查看进度" — 查询学习进度                       │
  │ "录入成绩 85" — 录入成绩/考勤                   │
  │ "检测冲突" — 检测课程/考试时间冲突               │
  │ "推荐资源" — 推荐学习资料                       │
  │ "生成报告" — 生成学业分析报告                   │
  │ "录入课表" — 管理个人课表数据                   │
  │ "退课"     — 调整/退选课程                      │
  │ 其他内容   — 闲聊/学科问答                      │
  ├────────────────────────────────────────────────┤
  │ 输入 /help  查看此帮助                         │
  │ 输入 /exit  退出系统                           │
  │ 输入 /redis 查看 Redis 连接状态                 │
  │ 输入 /state 查看当前会话状态                    │
  └────────────────────────────────────────────────┘
"""

# ── 核心逻辑 ────────────────────────────────────────

def setup(user_id: str) -> dict:
    """初始化数据库，加载 / 创建用户档案。"""
    logger.info("Initializing database...")
    init_database()

    user = get_user(user_id)
    if user:
        logger.info(f"Loaded existing user: {user['student_name']} ({user['major']})")
    else:
        logger.warning(f"User '{user_id}' not found in database. "
                       f"Run seed_data.sql or create a user first.")
        return {"found": False}

    return {"found": True, "user": user}


def run_once(user_id: str, session_id: str, user_input: str,
             has_minor: bool, has_major_timetable: bool) -> str:
    """
    执行一次完整的 LangGraph 运行。

    参数：
        user_id: 用户标识
        session_id: 会话 ID
        user_input: 用户输入文本
        has_minor: 是否已选辅修
        has_major_timetable: 是否已录入主修课表

    返回：
        系统响应文本
    """
    # 1. 尝试从 Redis 恢复会话状态
    redis_state = load_session_state(user_id, session_id)
    phase = redis_state.get("conversation_phase", ConversationPhase.IDLE.value)

    # 2. 构建初始 State
    state = create_initial_state(
        user_id=user_id,
        session_id=session_id,
        user_input=user_input,
        has_minor=has_minor if phase == ConversationPhase.IDLE.value else redis_state.get("has_minor_course", has_minor),
        has_major_timetable=has_major_timetable,
    )

    # 如果用 Redis 恢复了 phase，沿用
    if phase != ConversationPhase.IDLE.value:
        state["conversation_phase"] = phase
        # 恢复上一轮的中间结果
        state["current_recommendations"] = redis_state.get("current_recommendations", [])
        state["current_conflicts"] = redis_state.get("current_conflicts", [])
        state["current_resources"] = redis_state.get("current_resources", [])

    # 3. 运行 LangGraph
    app = get_app()
    result = app.invoke(state)

    # 4. 持久化会话状态到 Redis
    save_session_state(user_id, session_id, {
        "conversation_phase": result.get("conversation_phase", ConversationPhase.IDLE.value),
        "has_minor_course": result.get("has_minor_course", has_minor),
        "has_major_timetable": result.get("has_major_timetable", has_major_timetable),
        "current_recommendations": result.get("current_recommendations", []),
        "current_conflicts": result.get("current_conflicts", []),
        "current_resources": result.get("current_resources", []),
    })

    return result.get("final_response", "⚠️ 系统未生成有效响应。")


# ── CLI 交互循环 ────────────────────────────────────

def interactive_loop(user_id: str):
    """交互式 REPL 循环。"""
    session_id = str(uuid.uuid4())[:8]
    has_minor = False
    has_major_timetable = True

    # 从数据库加载用户辅修状态
    user = get_user(user_id)
    if user:
        has_minor = user.get("has_minor", 0) == 1
        minor_label = f"已选{user.get('minor_program', '')}" if has_minor else "未选"
        print(f"👤 {user['student_name']} | 主修: {user['major']} | 辅修: {minor_label}")
    else:
        print(f"👤 用户 {user_id}（未在数据库中找到，使用访客模式）")

    redis_ok = is_redis_available()
    print(f"{'🟢' if redis_ok else '🟡'} Redis: {'已连接' if redis_ok else '未连接（降级运行）'}")
    print(f"🆔 会话 ID: {session_id}")
    print(HELP_TEXT)
    print()

    while True:
        try:
            user_input = input("💬 You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        # 特殊命令
        if user_input.startswith("/exit"):
            print("👋 再见！")
            break
        if user_input.startswith("/help"):
            print(HELP_TEXT)
            continue
        if user_input.startswith("/redis"):
            ok = is_redis_available()
            print(f"Redis: {'🟢 已连接' if ok else '🟡 未连接（降级运行）'}")
            continue
        if user_input.startswith("/state"):
            user_fresh = get_user(user_id)
            if user_fresh:
                hm = user_fresh.get("has_minor", 0) == 1
                mp = user_fresh.get("minor_program", "")
                print(f"📦 SQLite: 辅修={'已选' + mp if hm else '未选'}")
            redis_state = load_session_state(user_id, session_id)
            if redis_state:
                print("📦 Redis Session State:")
                for k, v in redis_state.items():
                    val = str(v)[:80]
                    print(f"  {k}: {val}")
            else:
                print("📦 Redis: 无活跃会话状态")
            continue

        # 处理输入
        response = run_once(user_id, session_id, user_input, has_minor, has_major_timetable)

        # 每轮从 SQLite 同步辅修状态（调课/选课/重新推荐都会写库）
        user_fresh = get_user(user_id)
        if user_fresh:
            has_minor = user_fresh.get("has_minor", 0) == 1
            minor_label = f"已选{user_fresh.get('minor_program', '')}" if has_minor else "未选"
        else:
            minor_label = "未选"

        print(f"\n🤖 Agent >\n{response}")
        print(f"\n📊 辅修: {minor_label}\n")


# ── 入口 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="大学生辅修学习规划与跟踪多智能体系统"
    )
    parser.add_argument("--user-id", default="u001",
                        help="用户 ID（默认: u001）")
    args = parser.parse_args()

    print(BANNER)

    # 初始化
    setup_result = setup(args.user_id)
    if not setup_result["found"]:
        print(f"⚠️ 用户 '{args.user_id}' 不存在，使用访客模式。")
        print("  → 数据库中的用户: u001（种子数据）")
        print("  → 可以用 seed_data.sql 手动导入用户\n")

    # 进入交互循环
    interactive_loop(args.user_id)


if __name__ == "__main__":
    main()
