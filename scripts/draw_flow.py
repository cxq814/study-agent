"""绘制资源推荐流程图"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'PingFang SC']
plt.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(1, 1, figsize=(16, 22))
ax.set_xlim(0, 16)
ax.set_ylim(0, 22)
ax.axis('off')

# 颜色方案
COLORS = {
    'start_end': '#667eea',
    'process': '#f0f2ff',
    'decision': '#fff3e0',
    'data': '#e8f5e9',
    'llm': '#fce4ec',
    'arrow': '#555',
    'text': '#333',
    'border_process': '#5568d3',
    'border_decision': '#e65100',
    'border_data': '#2e7d32',
    'border_llm': '#c62828',
}

def draw_box(ax, x, y, w, h, text, color_key='process', fontsize=10, bold=False):
    """绘制圆角矩形"""
    bg = COLORS[color_key]
    border = COLORS.get(f'border_{color_key}', COLORS['border_process'])
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                          boxstyle="round,pad=0.15",
                          facecolor=bg, edgecolor=border,
                          linewidth=1.5, alpha=0.95)
    ax.add_patch(box)
    weight = 'bold' if bold else 'normal'
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize,
            color=COLORS['text'], fontweight=weight, wrap=True)

def draw_arrow(ax, x1, y1, x2, y2, label=''):
    """绘制箭头"""
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=COLORS['arrow'],
                               lw=1.5, connectionstyle='arc3,rad=0'))
    if label:
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mid_x + 0.3, mid_y, label, fontsize=8, color='#888',
                ha='left', va='center', style='italic')

# ===== 绘制流程图 =====
# 中心线 x=8
cx = 8

# === 开始 ===
draw_box(ax, cx, 21.3, 3.5, 0.8, '用户输入"推荐资源"', 'start_end', 11, True)

# === 读取学习进度 ===
draw_arrow(ax, cx, 20.9, cx, 20.3)
draw_box(ax, cx, 20.0, 3.0, 0.7, '读取 SQLite 学习进度数据', 'data', 10)

# === 构建课程摘要 ===
draw_arrow(ax, cx, 19.65, cx, 19.05)
draw_box(ax, cx, 18.75, 3.0, 0.7, '构建课程摘要 course_summary', 'process', 10)

# === 识别薄弱点 ===
draw_arrow(ax, cx, 18.4, cx, 17.8)
draw_box(ax, cx, 17.5, 3.0, 0.7, '识别薄弱点\n作业<70 / 小测<60 / 缺勤>=2', 'process', 9)

# === 判断分支 ===
draw_arrow(ax, cx, 17.15, cx, 16.65)
# 菱形判断
diamond = mpatches.Polygon([
    [cx, 16.5],    # 上
    [cx + 2.8, 16.0],  # 右
    [cx, 15.5],    # 下
    [cx - 2.8, 16.0],  # 左
], facecolor=COLORS['decision'], edgecolor=COLORS['border_decision'], linewidth=1.5)
ax.add_patch(diamond)
ax.text(cx, 16.0, '存在\n薄弱点?', ha='center', va='center', fontsize=9,
        color='#e65100', fontweight='bold')

# === 左侧：YES分支 (remedial) ===
left_x = 3.5
draw_arrow(ax, cx - 1.5, 15.9, left_x + 0.8, 15.0)
ax.text(cx - 2.5, 15.55, 'YES', fontsize=9, color='#c62828', fontweight='bold', ha='center')

draw_box(ax, left_x, 14.5, 3.0, 0.65, '薄弱点补救模式', 'decision', 10, True)

draw_arrow(ax, left_x, 14.15, left_x, 13.55)

# RAG 检索
draw_box(ax, left_x, 13.1, 3.5, 0.7, 'RAG 知识库检索\n习题 + 笔记 + 网课', 'process', 9)
draw_arrow(ax, left_x, 12.75, left_x, 12.15)

# 爬虫
draw_box(ax, left_x, 11.7, 3.5, 0.7, 'Scraper 爬虫数据\n关键词匹配搜索', 'data', 9)
draw_arrow(ax, left_x, 11.35, left_x, 10.75)

# LLM
draw_box(ax, left_x, 10.3, 3.5, 0.7, 'LLM 提示词生成\n基础巩固推荐', 'llm', 9)

# 链接验证
draw_arrow(ax, left_x, 9.95, left_x, 9.3)
draw_box(ax, left_x, 8.85, 3.8, 0.7, '链接验证后处理\n过滤虚构URL + 强制注入', 'process', 9)

# === 右侧：NO分支 (advanced) ===
right_x = 12.5
draw_arrow(ax, cx + 1.5, 15.9, right_x - 0.8, 15.0)
ax.text(cx + 2.5, 15.55, 'NO', fontsize=9, color='#2e7d32', fontweight='bold', ha='center')

draw_box(ax, right_x, 14.5, 3.0, 0.65, '进阶拓展模式', 'data', 10, True)

draw_arrow(ax, right_x, 14.15, right_x, 13.55)

draw_box(ax, right_x, 13.1, 3.8, 0.7, '遍历所有课程 → RAG检索\n构建课程-资源映射', 'process', 9)
draw_arrow(ax, right_x, 12.75, right_x, 12.15)

draw_box(ax, right_x, 11.7, 3.8, 0.7, 'Scraper 爬虫搜索\n按课程代码匹配真实链接', 'data', 9)
draw_arrow(ax, right_x, 11.35, right_x, 10.75)

draw_box(ax, right_x, 10.3, 3.8, 0.7, 'LLM 提示词生成\n学术前沿/竞赛/项目/交叉学科', 'llm', 9)
draw_arrow(ax, right_x, 9.95, right_x, 9.3)
draw_box(ax, right_x, 8.85, 3.8, 0.7, '链接验证后处理\n过滤虚构URL + 强制注入', 'process', 9)

# === 汇合 ===
draw_arrow(ax, left_x, 8.5, left_x, 7.8)
draw_arrow(ax, right_x, 8.5, right_x, 7.8)
# 水平箭头汇合到中心
ax.annotate('', xy=(cx, 7.4), xytext=(left_x, 7.7),
            arrowprops=dict(arrowstyle='->', color=COLORS['arrow'], lw=1.5,
                           connectionstyle='arc3,rad=-0.15'))
ax.annotate('', xy=(cx, 7.4), xytext=(right_x, 7.7),
            arrowprops=dict(arrowstyle='->', color=COLORS['arrow'], lw=1.5,
                           connectionstyle='arc3,rad=0.15'))

# === 格式化输出 ===
draw_box(ax, cx, 7.0, 4.5, 0.7, '格式化输出\n[RAG统计 + 爬虫统计 + 外部链接]', 'process', 9)

# === 显示结果 ===
draw_arrow(ax, cx, 6.65, cx, 6.1)
draw_box(ax, cx, 5.7, 4.0, 0.8, '展示推荐结果 + 反馈提问\n"这些推荐对你有帮助吗？"', 'start_end', 10, True)

# === 反馈循环 ===
draw_arrow(ax, cx, 5.3, cx, 4.75)
draw_box(ax, cx, 4.35, 3.5, 0.65, 'AWAIT_RESOURCE_FEEDBACK\n等待用户反馈', 'decision', 9)

draw_arrow(ax, cx, 4.0, cx, 3.3)
draw_box(ax, cx, 2.9, 3.5, 0.65, '根据反馈调整推荐\n"不够详细"→重新生成', 'process', 9)

draw_arrow(ax, cx, 2.55, cx, 2.1)
draw_box(ax, cx, 1.7, 3.0, 0.7, '对话结束 / 返回 IDLE', 'start_end', 10)

# ===== 图例 =====
legend_y = 0.7
legend_items = [
    ('start_end', '开始 / 结束'),
    ('process', '处理步骤'),
    ('data', '数据 / 进阶模式'),
    ('decision', '判断 / 补救模式'),
    ('llm', 'LLM 调用'),
]
for i, (key, label) in enumerate(legend_items):
    lx = 1.5 + i * 2.8
    box = FancyBboxPatch((lx - 0.5, legend_y - 0.15), 1.0, 0.3,
                          boxstyle="round,pad=0.05",
                          facecolor=COLORS[key], edgecolor=COLORS.get(f'border_{key}', '#888'),
                          linewidth=1)
    ax.add_patch(box)
    ax.text(lx + 0.7, legend_y, label, fontsize=8, va='center', color='#555')

ax.text(cx, 0.2, '图4-1  资源推荐 Agent 工作流程图', ha='center', fontsize=12,
        fontweight='bold', color=COLORS['text'])

plt.tight_layout()
out = 'docs/资源推荐流程图.png'
plt.savefig(out, dpi=200, bbox_inches='tight', facecolor='white', edgecolor='none')
print(f'Done: {out}')
