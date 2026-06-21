"""绘制 LangGraph 智能体工作流图 + 系统总体架构图"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Arc
import numpy as np

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'PingFang SC']
plt.rcParams['axes.unicode_minus'] = False

C = {
    'purple': '#667eea', 'blue': '#4a90d9', 'green': '#43a047',
    'orange': '#ef6c00', 'red': '#e53935', 'teal': '#00897b',
    'pink': '#d81b60', 'gray': '#78909c',
    'bg_purple': '#f0f2ff', 'bg_blue': '#e3f2fd',
    'bg_green': '#e8f5e9', 'bg_orange': '#fff3e0',
    'bg_red': '#fce4ec', 'bg_teal': '#e0f2f1',
    'bg_pink': '#fce4ec', 'bg_gray': '#eceff1',
}

def box(ax, x, y, w, h, text, color='purple', fs=9, bold=False):
    bg = C[f'bg_{color}']
    bd = C[color]
    b = FancyBboxPatch((x-w/2, y-h/2), w, h, boxstyle="round,pad=0.12",
                        facecolor=bg, edgecolor=bd, linewidth=1.5)
    ax.add_patch(b)
    ax.text(x, y, text, ha='center', va='center', fontsize=fs, fontweight='bold' if bold else 'normal', color='#333')

def arrow(ax, x1, y1, x2, y2):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color='#666', lw=1.3))

def diamond(ax, x, y, w, h, text, color='orange', fs=8):
    points = [(x, y+h), (x+w, y), (x, y-h), (x-w, y)]
    d = mpatches.Polygon(points, facecolor=C[f'bg_{color}'], edgecolor=C[color], linewidth=1.5)
    ax.add_patch(d)
    ax.text(x, y, text, ha='center', va='center', fontsize=fs, fontweight='bold', color=C[color])

# =====================================================
# 图1: LangGraph 智能体工作流图
# =====================================================
fig1, ax1 = plt.subplots(1, 1, figsize=(20, 16))
ax1.set_xlim(0, 20)
ax1.set_ylim(0, 16)
ax1.axis('off')

# 入口
box(ax1, 10, 15.3, 2.5, 0.7, '用户输入\nUser Input', 'purple', 10, True)

# Router
arrow(ax1, 10, 14.9, 10, 14.3)
box(ax1, 10, 14.0, 3.2, 0.7, 'Router 路由节点\n关键词加权 + 意图分类', 'purple', 10, True)

# 9 intents branching from router
arrow(ax1, 10, 13.65, 10, 13.2)

# 9 agents in 3 rows
agents = [
    # row 1
    [(1.5, 12.5, 'course_planner\n课程规划', 'blue'),
     (4.5, 12.5, 'progress_tracker\n进度追踪', 'green'),
     (7.5, 12.5, 'conflict_checker\n冲突检测', 'orange')],
    # row 2
    [(10, 12.5, 'resource_recommender\n资源推荐', 'red'),
     (13.0, 12.5, 'report_generator\n报告生成', 'teal'),
     (16.0, 12.5, 'data_manager\n数据管理', 'pink')],
    # row 3
    [(3.0, 11.0, 'course_adjuster\n课表调整', 'gray'),
     (7.5, 11.0, 'chat_qa\n对话问答', 'purple'),
     (12.0, 11.0, 'conflict_gate\n冲突检测门', 'orange')],
]

# Draw router to agent arrows (simplified — horizontal + vertical from center)
# Draw all agent boxes
for row in agents:
    for x, y, label, color in row:
        box(ax1, x, y, 2.6, 0.8, label, color, 8)

# Router to agent connections (fan out)
for row in agents:
    for x, y, label, color in row:
        arrow(ax1, 10, 12.8, x, y + 0.4)

# All agents → converge back
conv_x, conv_y = 10, 9.5
for row in agents:
    for x, y, label, color in row:
        arrow(ax1, x, y - 0.4, conv_x, conv_y + 0.3)

# Response formation
box(ax1, 10, 9.0, 3.5, 0.7, 'final_response 汇集\n写入 AgentState', 'green', 10, True)

# Phase check
arrow(ax1, 10, 8.65, 10, 8.15)
diamond(ax1, 10, 7.8, 3.0, 0.65, '多轮对话?\nphase check', 'orange', 8)

# YES → loop back
ax1.annotate('YES', xy=(15, 7.8), xytext=(9.2, 7.8), fontsize=8,
            color=C['orange'], fontweight='bold', ha='center',
            arrowprops=dict(arrowstyle='->', color=C['orange'], lw=1.2,
                           connectionstyle='arc3,rad=0.3'))
ax1.text(17.2, 10.5, '返回 Router\n(携带 phase)', fontsize=8, color=C['orange'],
         ha='center', va='center',
         bbox=dict(boxstyle='round', facecolor=C['bg_orange'], alpha=0.5))

# NO → output
arrow(ax1, 10, 7.45, 10, 6.9)
box(ax1, 10, 6.5, 3.0, 0.7, '输出到 Streamlit\n用户看到回复', 'purple', 10, True)

# Phase labels on right
ax1.text(18.8, 12.5, '7 种对话阶段', fontsize=9, color='#888', ha='center')
phases = ['AWAIT_COURSE_SELECTION', 'AWAIT_NEW_INTERESTS',
          'AWAIT_PROGRESS_INPUT', 'AWAIT_DATA_INPUT',
          'AWAIT_CONFLICT_DECISION', 'AWAIT_RESOURCE_FEEDBACK',
          'AWAIT_COURSE_ADJUSTMENT']
for i, ph in enumerate(phases):
    ax1.text(18.8, 12.0 - i*0.5, ph, fontsize=6.5, color='#aaa', ha='center')

# Title
ax1.text(10, 15.9, '图3-2  LangGraph 多智能体工作流图', ha='center', fontsize=14,
        fontweight='bold', color='#333')

plt.tight_layout()
fig1.savefig('docs/LangGraph工作流图.png', dpi=200, bbox_inches='tight', facecolor='white')
print('Done: docs/LangGraph工作流图.png')

# =====================================================
# 图2: 系统总体架构图
# =====================================================
fig2, ax2 = plt.subplots(1, 1, figsize=(18, 14))
ax2.set_xlim(0, 18)
ax2.set_ylim(0, 14)
ax2.axis('off')

# ---- 展示层 ----
layer_h = 2.0
box(ax2, 9, 13.0, 14, 1.2, '', 'purple', 1)
ax2.text(9, 13.4, '展示层 Presentation Layer', ha='center', fontsize=12, fontweight='bold', color='white',
         bbox=dict(facecolor=C['purple'], edgecolor='none', boxstyle='round,pad=0.3'))
# Components
for i, (x, comp) in enumerate([(3, 'Streamlit App\n对话式 UI'), (7, '侧边栏\n资料/导航'),
                                (11, '数据看板\n课表/进度/冲突'), (15, 'Plotly\n成绩趋势图')]):
    box(ax2, x, 12.3, 3.2, 0.9, comp, 'purple', 8)

# Arrow down
arrow(ax2, 9, 12.0, 9, 11.3)

# ---- 智能体层 ----
ax2.text(9, 11.1, '智能体层 Agent Layer', ha='center', fontsize=12, fontweight='bold', color='white',
         bbox=dict(facecolor=C['red'], edgecolor='none', boxstyle='round,pad=0.3'))
box(ax2, 9, 10.3, 16, 1.2, '', 'red', 1)
agents_row = [
    (2.0, 'Router\n路由', 'red'), (4.4, 'Course\nPlanner', 'blue'),
    (6.8, 'Progress\nTracker', 'green'), (9.2, 'Conflict\nChecker', 'orange'),
    (11.6, 'Resource\nRecommender', 'red'), (14.0, 'Report\nGenerator', 'teal'),
    (16.4, 'Data\nManager', 'pink'),
]
for x, label, color in agents_row:
    box(ax2, x, 10.3, 2.2, 1.0, label, color, 7.5)

# LangGraph bar
box(ax2, 9, 9.3, 14, 0.6, 'LangGraph StateGraph — 条件路由 + 状态管理 + 多轮对话', 'orange', 8, True)

arrow(ax2, 9, 9.0, 9, 8.3)

# ---- 工具与基础设施层 ----
ax2.text(9, 8.1, '工具与基础设施层 Tools & Infrastructure', ha='center', fontsize=12, fontweight='bold',
         color='white', bbox=dict(facecolor=C['teal'], edgecolor='none', boxstyle='round,pad=0.3'))
box(ax2, 9, 7.3, 16, 1.2, '', 'teal', 1)
tools_row = [
    (2.0, 'LLM 调用封装\n3层降级', 'teal'), (5.0, '嵌入模型\n双层降级', 'teal'),
    (8.0, '爬虫模块\nMOOC+B站+Kaggle', 'teal'), (11.0, '统计分析\n趋势+风险', 'teal'),
    (14.0, 'RAG 工具集\n检索+缓存', 'teal'), (16.5, '导出工具\nMarkdown+JSON', 'teal'),
]
for x, label, color in tools_row:
    box(ax2, x, 7.3, 2.6, 0.9, label, color, 7.5)

arrow(ax2, 9, 6.7, 9, 6.0)

# ---- 数据存储层 ----
ax2.text(9, 5.8, '数据存储层 Data Storage', ha='center', fontsize=12, fontweight='bold', color='white',
         bbox=dict(facecolor=C['green'], edgecolor='none', boxstyle='round,pad=0.3'))
box(ax2, 9, 5.0, 16, 1.2, '', 'green', 1)
data_row = [
    (3.0, 'SQLite\n用户/课表/进度/冲突', 'green'),
    (9.0, 'ChromaDB\n29个MD知识库文档\n向量语义检索', 'green'),
    (15.0, 'Redis\n会话缓存+RAG缓存\n降级→内存字典', 'green'),
]
for x, label, color in data_row:
    box(ax2, x, 5.0, 4.5, 1.0, label, color, 8)

# ---- 外部服务 ----
ax2.text(1.5, 3.5, '外部服务', fontsize=10, fontweight='bold', color='#555', rotation=90, va='center')
ext_services = [
    (4.0, 'DeepSeek API\n主力推理模型'),
    (9.0, 'Anthropic API\n备用推理模型'),
    (14.0, 'BAAI Embedding\nbge-small-zh-v1.5'),
]
for x, label in ext_services:
    box(ax2, x, 3.0, 3.5, 0.9, label, 'gray', 8)

# Connections: tools → external
for x, _ in ext_services:
    arrow(ax2, x, 3.5, x, 4.4)

# Color legend
ly = 1.5
for i, (color, label) in enumerate([
    ('purple', '展示层'), ('red', '智能体层'), ('teal', '工具层'),
    ('green', '存储层'), ('gray', '外部服务')]):
    lx = 2.5 + i * 3.0
    b = FancyBboxPatch((lx-1.0, ly-0.2), 2.0, 0.4, boxstyle="round,pad=0.05",
                        facecolor=C[f'bg_{color}'], edgecolor=C[color], linewidth=1)
    ax2.add_patch(b)
    ax2.text(lx, ly, label, fontsize=8, ha='center', va='center', color=C[color], fontweight='bold')

ax2.text(9, 0.8, '图3-1  系统总体架构图', ha='center', fontsize=14, fontweight='bold', color='#333')

plt.tight_layout()
fig2.savefig('docs/系统总体架构图.png', dpi=200, bbox_inches='tight', facecolor='white')
print('Done: docs/系统总体架构图.png')
