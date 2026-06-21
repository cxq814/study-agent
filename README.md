# 学业规划助手

基于 LangGraph 的辅修学业智能规划与管理系统，帮助大学生选择辅修专业、管理课表、追踪学习进度。

## 功能概览

| 功能 | 说明 |
|------|------|
| 🎯 推荐辅修 | 根据兴趣方向推荐辅修专业及课程方案 |
| 📅 课表管理 | 录入主修课程、冲突检测、退课换课 |
| 📊 进度追踪 | 记录成绩/作业/小测/出勤，可视化趋势 |
| 📚 资源推荐 | 根据薄弱课程推荐习题、笔记、网课 |
| 📝 报告生成 | 自动生成综合学业报告并导出 Markdown |
| 💬 智能问答 | 基于知识库的课程咨询与选课指导 |

## 快速开始

### 环境要求

- Python 3.10+
- Redis（可选，不装自动降级为内存存储）

### 安装

```bash
git clone https://github.com/cxq814/study-agent.git
cd study-agent
pip install -r requirements.txt
```

### 配置

```bash
# 复制环境变量模板
cp .env.template .env
# 编辑 .env 填入你的 API Key（可选，不填也能用）
```

### 运行

**命令行模式：**

```bash
python app.py
```

**Web 界面模式：**

```bash
streamlit run streamlit_app.py
```

## 项目结构

```
study_agent/
├── app.py                 # 命令行入口
├── streamlit_app.py       # Web 界面入口
├── config/                # 配置管理
├── src/
│   ├── agents/            # LangGraph 智能体（路由/规划/冲突检测/报告生成等）
│   ├── graph/             # 状态图定义与工作流编排
│   ├── rag/               # RAG 检索增强（嵌入/索引/检索）
│   ├── scraper/           # 网页爬取管道
│   ├── storage/           # 数据持久化（SQLite + Redis）
│   └── tools/             # 工具函数集合
├── data/
│   ├── knowledge_base/    # 知识库（课程信息、习题、笔记、网课推荐）
│   ├── database/          # 数据库初始化脚本
│   └── exports/           # 导出报告
├── scripts/               # 辅助脚本（架构图/流程图/论文生成）
├── tests/                 # 单元测试
└── docs/                  # 设计文档与用户手册
```

## 技术栈

- **框架**: LangGraph（多智能体编排）
- **LLM**: DeepSeek / Anthropic Claude（可插拔）
- **RAG**: ChromaDB + sentence-transformers
- **界面**: Streamlit
- **存储**: SQLite + Redis
- **爬虫**: requests + BeautifulSoup

## 文档

- [用户手册](docs/user_manual.md)
- [系统设计文档](系统设计文档_v2.md)
- [LLM 接入设计](LLM接入设计文档.md)

## License

MIT
