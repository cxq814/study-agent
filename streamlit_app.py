"""
Streamlit 前端 — 大学生辅修学习规划与跟踪多智能体系统。

启动: streamlit run streamlit_app.py
"""

import sys
import os
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import LLM_CONFIG
from src.tools.llm_tools import llm_is_available
from src.storage import init_database, get_user, is_redis_available
from src.storage.sqlite_client import (
    get_timetable, get_study_progress, get_conflict_history,
    upsert_user,
)
from src.graph import create_initial_state, get_app, ConversationPhase
from src.graph.workflow import reset_app
from src.tools.profile_tools import MAJOR_OPTIONS, GRADE_OPTIONS, validate_profile, build_user_updates

# ── 页面配置 ──

st.set_page_config(
    page_title="学业规划助手",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 自定义 CSS ──

st.markdown("""
<style>
    section[data-testid="stSidebar"] {
        background: #fafbfd !important;
        border-right: 1px solid #eef0f6 !important;
    }
    .stChatMessage {
        border-radius: 14px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.04);
    }
    /* 侧边栏导航按钮 */
    div[data-testid="stSidebar"] button[kind="secondary"] {
        width: 100%;
        text-align: left;
        border: none;
        border-radius: 8px;
        padding: 9px 12px;
        margin: 1px 0;
        font-size: 13px;
        font-weight: 500;
        background: transparent;
        color: #444;
        transition: all 0.12s ease;
    }
    div[data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #f0f2ff;
        color: #5568d3;
        transform: translateX(2px);
    }
    /* 通用圆角 */
    .stButton > button { border-radius: 10px !important; }
    .stPopover { border-radius: 14px !important; }
    .stTextInput input { border-radius: 8px !important; }
    .stSelectbox [data-baseweb="select"] { border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)

# ── 初始化 ──

def _get_src_max_mtime() -> float:
    """扫描 src/ 目录下所有 .py 文件的最新修改时间。"""
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    max_mtime = 0.0
    for root, _dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                try:
                    mtime = os.path.getmtime(os.path.join(root, f))
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
    return max_mtime

@st.cache_resource
def _get_cached_app(_code_version: float):
    """缓存 app 实例。仅当 _code_version（源码 mtime）变化时才重建。"""
    reset_app()
    init_database()
    return get_app()

# 代码版本号 = src/ 下最新 .py 文件的修改时间
# 代码没变 → 版本号不变 → st.cache_resource 命中缓存 → 极快
# 代码变了 → 版本号变化 → 缓存失效 → reset_app() + get_app() 重建
_code_ver = _get_src_max_mtime()
with st.spinner("加载模型..."):
    st.session_state.app = _get_cached_app(_code_ver)

# 初始化持久化状态（仅首次运行，后续交互保留）
if "user_id" not in st.session_state:
    st.session_state.user_id = "u001"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conv_phase" not in st.session_state:
    st.session_state.conv_phase = ConversationPhase.IDLE.value
if "current_recommendations" not in st.session_state:
    st.session_state.current_recommendations = []
if "current_conflicts" not in st.session_state:
    st.session_state.current_conflicts = []
if "current_resources" not in st.session_state:
    st.session_state.current_resources = []
if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

try:
    USER_ID = st.session_state.user_id
except Exception:
    USER_ID = "u001"


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


def _refresh_user_flags() -> tuple:
    """从 SQLite 读取最新用户状态。"""
    user = get_user(USER_ID)
    hm = user.get("has_minor", 0) == 1 if user else False
    return user, hm


def _build_state(user_input: str) -> dict:
    """构建 AgentState，复用持久化的多轮状态。"""
    user, hm = _refresh_user_flags()
    state = create_initial_state(USER_ID, st.session_state.session_id, user_input, hm, True)
    # 注入上一轮的对话阶段和中间结果
    state["conversation_phase"] = st.session_state.conv_phase
    state["current_recommendations"] = st.session_state.current_recommendations
    state["current_conflicts"] = st.session_state.current_conflicts
    state["current_resources"] = st.session_state.current_resources
    return state


def _save_state(result: dict):
    """从 invoke 结果中提取并持久化多轮状态。"""
    st.session_state.conv_phase = result.get("conversation_phase", ConversationPhase.IDLE.value)
    st.session_state.current_recommendations = result.get("current_recommendations", [])
    st.session_state.current_conflicts = result.get("current_conflicts", [])
    st.session_state.current_resources = result.get("current_resources", [])
    # 同步 has_minor 到 SQLite → 侧边栏刷新
    if result.get("has_minor_course") is not None:
        pass  # agent 会自己写 SQLite


# ── 侧边栏 ──

with st.sidebar:
    user = get_user(USER_ID)
    name = user.get("student_name", "").strip() if user else ""
    major = user.get("major", "").strip() if user else ""
    grade = user.get("grade", "").strip() if user else ""
    timetable = get_timetable(USER_ID)
    has_minor = user.get("has_minor", 0) == 1 if user else False
    if not has_minor:
        has_minor = any(c.get("course_type") == "minor" for c in (timetable or []))
    minor_text = user.get("minor_program") or "已选择" if has_minor else "未选辅修"

    # ======== HEADER: 用户信息 ========
    st.markdown(f"""
    <div style="padding:16px 12px 14px;">
        <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
                <div style="font-size:17px;font-weight:700;color:#1a1a2e;">{name or '未命名'}</div>
                <div style="font-size:12px;color:#888;margin-top:3px;">{major}{' · '+grade if grade else ''}</div>
            </div>
            <div style="font-size:22px;cursor:pointer;" id="profile-edit-btn">
                ⚙
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ======== 辅修状态 Tag ========
    c_tag, c_edit = st.columns([5, 1])
    with c_tag:
        tag_bg = "#f0f2ff" if has_minor else "#f5f5f5"
        tag_color = "#5568d3" if has_minor else "#999"
        st.markdown(f"""
        <div style="background:{tag_bg};color:{tag_color};border-radius:6px;
                    padding:4px 10px;font-size:12px;display:inline-block;font-weight:500;">
            {minor_text}
        </div>
        """, unsafe_allow_html=True)
    with c_edit:
        with st.popover("", icon=":material/edit:"):
            st.caption("编辑个人资料")
            with st.form("profile_form"):
                s_name = st.text_input("姓名", value=name)
                s_major = st.selectbox("主修", MAJOR_OPTIONS, index=_major_index(major) if major else 0)
                s_grade = st.selectbox("年级", GRADE_OPTIONS, index=_grade_index(grade) if grade else 0)
                cur = _parse_interests(user.get("interests", "")) if user else []
                s_int = st.text_input("兴趣", value="，".join(cur) if cur else "", placeholder="金融，数据科学")
                if st.form_submit_button("保存", use_container_width=True):
                    ok, err = validate_profile(s_name, s_major)
                    if not ok:
                        st.error(err)
                    else:
                        upd = build_user_updates(s_name, s_major, s_grade, s_int, user or {})
                        upd["user_id"] = USER_ID
                        upsert_user(**upd)
                        st.success("已保存")
                        st.rerun()

    st.divider()

    # ======== 功能导航 ========
    nav_labels = [
        "推荐辅修", "查看进度", "检测冲突",
        "推荐资源", "生成报告", "我的课表",
        "退课", "帮助",
    ]
    for label in nav_labels:
        if st.button(label, key=f"nav_{label}", use_container_width=True):
            st.session_state.pending_input = label

    st.divider()

    # ======== 底部 ========
    c1, c2 = st.columns(2)
    with c1:
        llm_ok = llm_is_available()
        st.markdown(f"<div style='font-size:11px;color:#888;'>LLM {'·' if llm_ok else '· 离线'}</div>",
                    unsafe_allow_html=True)
    with c2:
        try:
            n = len(st.session_state.messages)
        except Exception:
            n = 0
        st.markdown(f"<div style='text-align:right;font-size:11px;color:#888;'>{n} 条消息</div>",
                    unsafe_allow_html=True)

    if st.button("新对话", use_container_width=True):
        st.session_state.messages = []
        st.session_state.conv_phase = ConversationPhase.IDLE.value
        st.session_state.current_recommendations = []
        st.session_state.current_conflicts = []
        st.session_state.current_resources = []
        st.rerun()


# ── 主聊天区 ──

st.markdown('<div class="main-title">🎓 学业规划助手</div>', unsafe_allow_html=True)
st.caption("大学生辅修学习规划与跟踪 | LangGraph 多智能体系统")

# 客户端加载遮罩：用户发送消息时立即显示，避免白屏等待
st.markdown("""
<div id="llm-loading-overlay" style="
    display:none; position:fixed; top:0; left:0; width:100%; height:100%;
    background:rgba(255,255,255,0.92); z-index:99999;
    align-items:center; justify-content:center; flex-direction:column;
">
    <div style="font-size:48px;">🤔</div>
    <div style="font-size:18px; color:#555; margin-top:16px;">AI 正在思考...</div>
</div>
<script>
(function() {
    var overlay = document.getElementById('llm-loading-overlay');
    function showOverlay() { overlay.style.display = 'flex'; }
    // 表单提交（chat_input）
    var forms = document.getElementsByTagName('form');
    for (var i = 0; i < forms.length; i++) {
        forms[i].addEventListener('submit', showOverlay);
    }
    // 所有按钮点击（包括侧边栏导航、新对话等）
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('button');
        if (btn && !btn.disabled) {
            var text = btn.textContent || '';
            // 排除不会触发长时间等待的按钮
            if (!text.includes('▶') && !text.includes('展开')) {
                showOverlay();
            }
        }
        // Ant Design menu item 点击
        var menuItem = e.target.closest('[class*=\"menu-item\"]');
        if (menuItem) { showOverlay(); }
    });
})();
</script>
""", unsafe_allow_html=True)

# 首次使用引导：用户名为空时显示欢迎面板
user = get_user(USER_ID)
if not user or not (user.get("student_name") or "").strip():
    with st.container():
        st.markdown("""
        <div style="background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%);
                    border: 1px solid #e0e0ff; border-radius: 16px; padding: 28px 32px;
                    margin-bottom: 16px;">
            <h3 style="margin-top: 0;">👋 欢迎使用学业规划助手！</h3>
            <p>请先在 <b>左侧边栏 → 👤 个人资料</b> 中填写你的基本信息，然后就可以开始：</p>
            <table style="border: none; width: 100%;">
                <tr><td style="width: 32px;">📋</td><td><b>推荐辅修</b> — 根据你的兴趣推荐辅修方案</td></tr>
                <tr><td>📈</td><td><b>查看进度</b> — 追踪各课程学习情况</td></tr>
                <tr><td>📚</td><td><b>推荐资源</b> — 获取个性化学习资源</td></tr>
                <tr><td>⚠️</td><td><b>检测冲突</b> — 检查主修/辅修时间冲突</td></tr>
            </table>
        </div>
        """, unsafe_allow_html=True)

# 渲染消息
try:
    _msgs = st.session_state.messages
except Exception:
    _msgs = []
for msg in _msgs:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 输入框
user_input = st.chat_input("输入你的需求...")

# 处理快捷操作（导航菜单点击 / 快捷按钮）
try:
    _pending = st.session_state.pending_input
except Exception:
    _pending = None
if _pending:
    user_input = _pending
    st.session_state.pending_input = None

if user_input:
    # 显示用户消息
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    # 调用 LangGraph（复用持久化的多轮状态）
    state = _build_state(user_input)
    with st.spinner("思考中..."):
        result = st.session_state.app.invoke(state)

    response = result.get("final_response", "处理出错，请重试。")
    _save_state(result)

    with st.chat_message("assistant"):
        st.markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})


# ── 数据看板（折叠，默认收起，避免干扰对话）──

with st.expander("📊 数据看板（课表 / 进度 / 冲突）", expanded=False):
    tab1, tab2, tab3 = st.tabs(["📅 课表", "📈 进度", "⚠️ 冲突"])

    with tab1:
        timetable = get_timetable(USER_ID)
        if timetable:
            # 课程代码 → 中文名 映射
            from src.storage.sqlite_client import get_course as _get_course
            _name_cache = {}
            def _course_name(code: str) -> str:
                if code not in _name_cache:
                    c = _get_course(code)
                    _name_cache[code] = c.get("course_name", code) if c else code
                return _name_cache[code]

            day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
            for day_idx in range(1, 8):
                day_courses = [
                    c for c in timetable if c.get("day_of_week") == day_idx
                ]
                if day_courses:
                    st.write(f"**{day_names[day_idx]}**")
                    for c in sorted(day_courses, key=lambda x: x.get("period_start", 0)):
                        ctype = c.get("course_type", "")
                        label = "必修" if ctype == "major" else "辅修"
                        badge = "🔵" if ctype == "major" else "🟡"
                        name = _course_name(c.get("course_code", ""))
                        loc = c.get("location", "")
                        ps = c.get("period_start", "?")
                        pe = c.get("period_end", "?")
                        st.markdown(
                            f"{badge} **{name}**（{c.get('course_code','')}）"
                            f" 第{ps}-{pe}节 | {loc}"
                        )
        else:
            st.info("暂无课表数据。输入「录入课表」开始添加。")

    with tab2:
        progress = get_study_progress(USER_ID)
        if progress:
            st.metric("进度记录数", len(progress))
            # 表格形式展示
            import pandas as pd
            rows = []
            for p in progress[:50]:
                attn_map = {"present": "✅ 出勤", "absent": "❌ 缺勤", "late": "⚠️ 迟到"}
                rows.append({
                    "课程": p.get("course_code", ""),
                    "周次": f"第{p.get('week','?')}周",
                    "作业": f"{p.get('homework_score','-')}分",
                    "小测": f"{p.get('quiz_score','-')}分",
                    "出勤": attn_map.get(p.get("attendance", ""), p.get("attendance", "-")),
                })
            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True)
            try:
                import plotly.express as px
                df_plot = []
                for p in progress:
                    if p.get("homework_score") is not None:
                        df_plot.append({
                            "周次": p.get("week", 0),
                            "课程": p.get("course_code", ""),
                            "作业成绩": p.get("homework_score", 0),
                        })
                if df_plot:
                    df_plt = pd.DataFrame(df_plot)
                    fig = px.line(df_plt, x="周次", y="作业成绩", color="课程",
                                 title="📈 作业成绩趋势", markers=True)
                    st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                pass
        else:
            st.info("暂无进度数据。输入「录入成绩」开始记录。")

    with tab3:
        conflicts = get_conflict_history(USER_ID)
        # 复用课程名缓存
        from src.storage.sqlite_client import get_course as _get_course2
        _cn = {}
        def _cname(code: str) -> str:
            if code not in _cn:
                c = _get_course2(code)
                _cn[code] = c.get("course_name", code) if c else code
            return _cn[code]

        if conflicts:
            for c in conflicts[:10]:
                sev = c.get("severity", "info")
                sev_cfg = {
                    "critical": ("🔴", "严重冲突：建议优先处理"),
                    "warning": ("🟡", "轻微冲突：建议留意"),
                    "info": ("🔵", "提示"),
                }
                icon, sev_label = sev_cfg.get(sev, ("⚪", sev))
                detail = c.get("overlap_detail", "")
                detail_text = ""
                if detail:
                    try:
                        import json
                        d = json.loads(detail) if isinstance(detail, str) else detail
                        major_info = d.get("major_schedule", "")
                        minor_info = d.get("minor_schedule", "")
                        detail_text = f"主修课时间：{major_info}，辅修课时间：{minor_info}"
                    except Exception:
                        detail_text = str(detail)
                ca = c.get("course_a", "?")
                cb = c.get("course_b", "?")
                st.markdown(
                    f"{icon} **{_cname(ca)}**（{ca}）与 **{_cname(cb)}**（{cb}）\n\n"
                    f"　　{sev_label}，{detail_text}"
                )
        else:
            st.success("暂无冲突记录。")
