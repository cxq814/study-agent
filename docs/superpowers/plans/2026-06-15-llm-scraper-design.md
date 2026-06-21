# DeepSeek LLM 接入 + 爬虫 + 项目优化 设计文档

> 版本: 1.0 | 日期: 2026-06-15 | 关联: 大学生辅修学习规划与跟踪多智能体系统

---

## 一、LLM 多供应商架构

### 1.1 现状问题

`llm_tools.py` 和 `settings.py` 均为 Anthropic 硬编码：

```python
# settings.py — 当前只有 Anthropic
LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
    "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "api_base": os.getenv("ANTHROPIC_BASE_URL", ""),
    ...
}

# llm_tools.py — 硬编码 import anthropic
client = anthropic.Anthropic(**client_kwargs)
```

### 1.2 目标架构

```
                    ┌──────────────────────────┐
                    │     llm_tools.py          │
                    │                           │
                    │  llm_call(prompt, ...,    │
                    │           role="task")    │ ← role 决定模型选择
                    │                           │
                    │  ┌─────────────────────┐  │
                    │  │ Provider Router     │  │
                    │  │                     │  │
                    │  │ role="task"    → deepseek-chat     │
                    │  │ role="reasoner"→ deepseek-reasoner │
                    │  │ role="chat"    → deepseek-chat     │
                    │  │ (不同 system prompt)   │
                    │  └────────┬────────────┘  │
                    │           │               │
                    │  ┌────────▼────────────┐  │
                    │  │ Fallback Chain      │  │
                    │  │ 1. DeepSeek API     │  │ ← 优先
                    │  │ 2. Anthropic API    │  │ ← 备用
                    │  │ 3. 模板回退          │  │ ← 兜底
                    │  └─────────────────────┘  │
                    └──────────────────────────┘
```

### 1.3 settings.py 改造

```python
# ── LLM 多供应商配置 ──

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")  # deepseek | anthropic | auto

# DeepSeek（OpenAI 兼容接口）
DEEPSEEK_CONFIG = {
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "task_model": os.getenv("DEEPSEEK_TASK_MODEL", "deepseek-chat"),
    "reasoner_model": os.getenv("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner"),
    "max_tokens": 4096,
    "temperature": 0.3,
}

# Anthropic（备用）
ANTHROPIC_CONFIG = {
    "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "base_url": os.getenv("ANTHROPIC_BASE_URL", ""),
    "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    "max_tokens": 2048,
    "temperature": 0.3,
}
```

### 1.4 llm_tools.py 改造

核心变更：`llm_call` 新增 `role` 参数，内部根据 role 选择模型和 provider。

```python
from openai import OpenAI  # DeepSeek 用 OpenAI SDK

# Provider 调用函数
def _call_deepseek(prompt, system, model, max_tokens, temperature) -> str:
    """DeepSeek API 调用（OpenAI 兼容）。"""
    client = OpenAI(
        api_key=DEEPSEEK_CONFIG["api_key"],
        base_url=DEEPSEEK_CONFIG["base_url"],
    )
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content

def _call_anthropic(prompt, system, model, max_tokens, temperature) -> str:
    """Anthropic API 调用（保留兼容）。"""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_CONFIG["api_key"])
    message = client.messages.create(
        model=model, max_tokens=max_tokens, temperature=temperature,
        system=system, messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text

# 模型路由
def _select_model(role: str) -> tuple:
    """
    根据调用角色选择 (provider, model, max_tokens)
    
    role:
      "task"     → deepseek-chat (报告/推荐/问答)
      "reasoner" → deepseek-reasoner (复杂推理)
      "chat"     → deepseek-chat + 闲聊 system prompt
    """
    if role == "reasoner":
        return ("deepseek", DEEPSEEK_CONFIG["reasoner_model"], 4096)
    else:
        return ("deepseek", DEEPSEEK_CONFIG["task_model"], 4096)

# 主入口
def llm_call(prompt, system="", fallback=None, role="task",
             model=None, max_tokens=None, temperature=None) -> str:
    """
    role: "task" | "reasoner" | "chat"
    
    降级链: DeepSeek → Anthropic → fallback()
    """
    provider, default_model, default_tokens = _select_model(role)
    model = model or default_model
    max_tokens = max_tokens or default_tokens
    
    # L1: DeepSeek
    if DEEPSEEK_CONFIG["api_key"]:
        try:
            return _call_deepseek(prompt, system, model, max_tokens, temperature or 0.3)
        except Exception as e:
            logger.warning("DeepSeek failed: %s", _classify_error(e))
    
    # L2: Anthropic
    if ANTHROPIC_CONFIG["api_key"]:
        try:
            return _call_anthropic(prompt, system, ANTHROPIC_CONFIG["model"],
                                   ANTHROPIC_CONFIG["max_tokens"], temperature or 0.3)
        except Exception as e:
            logger.warning("Anthropic failed: %s", _classify_error(e))
    
    # L3: Fallback
    return _do_fallback(fallback, "all_providers_unavailable")

# 便捷方法
def llm_chat(prompt, system="", fallback=None) -> str:
    """闲聊专用。"""
    chat_system = system or "你是一位友好的大学学业助手，用轻松自然的语气与同学交流。"
    return llm_call(prompt, system=chat_system, fallback=fallback, role="chat")

def llm_reasoner(prompt, system="", fallback=None) -> str:
    """复杂推理专用。"""
    return llm_call(prompt, system=system, fallback=fallback, role="reasoner")
```

### 1.5 模型分工

| role | 模型 | 适用场景 | system prompt |
|------|------|---------|---------------|
| `task` | deepseek-chat | 报告生成、资源推荐、学科问答 | 正式、专业 |
| `reasoner` | deepseek-reasoner | 冲突分析建议、学业风险评估 | 严谨、结构化 |
| `chat` | deepseek-chat | 闲聊、问候、功能引导 | 轻松、友好 |

---

## 二、爬虫系统设计

### 2.1 架构

```
src/scraper/
├── __init__.py
├── fetcher.py          # HTTP 请求 + 重试 + UA 伪装
├── parser.py           # HTML → 结构化数据
├── pipelines.py        # 数据清洗 + 课程匹配 + 写入文件
└── targets.py          # 爬取目标配置
```

### 2.2 爬取流程

```
1. 读取 targets.py → 爬取 URL 列表
2. fetcher.request(url) → HTML
3. parser.parse(html, target_config) → List[dict]
4. pipelines.clean(items) → 去重、去噪
5. pipelines.match_courses(items) → 按课程代码归类
6. 写入 data/scraped/{course_code}-{type}-{ts}.json
7. 更新 ChromaDB 索引（可选）
```

### 2.3 爬取目标

| 平台 | URL 示例 | 内容 |
|------|---------|------|
| 中国大学MOOC | `https://www.icourse163.org/search?key=金融学` | 课程简介、大纲、推荐教材 |
| B站搜索 | `https://api.bilibili.com/x/web-interface/search?keyword=公司金融` | 视频标题、UP主、播放量 |
| （可扩展） | — | — |

### 2.4 数据格式

爬取结果存入 `data/scraped/`，每条记录：

```json
{
    "id": "md5_hash",
    "source": "icourse163",
    "url": "https://...",
    "title": "金融学基础 - 中国大学MOOC",
    "description": "本课程介绍货币银行学基础...",
    "matched_courses": ["FIN101"],
    "keywords": ["金融学", "货币银行", "金融市场"],
    "scraped_at": "2026-06-15T10:30:00",
    "type": "course_intro"
}
```

### 2.5 与 RAG 集成

```
爬取内容 (data/scraped/*.json)
       │
       ▼
  ChromaDB 索引（新增 collection: scraped_resources）
       │
       ├──→ RAGToolset.search_scraped(query) → 爬取结果
       │
       └──→ RAGToolset.search_knowledge(query) → 本地知识库
                │
                ▼
         合并去重 → LLM 合成 → 最终推荐
```

---

## 三、增强资源推荐流程

### 3.1 当前问题

`resource_recommender.py` 的推荐逻辑：
1. 分析薄弱点（从 study_progress 找低分课程）
2. RAG 检索本地知识库
3. 硬编码模板拼接输出

**不足**：模板固定、内容陈旧、无外部信息。

### 3.2 改造方案

```
用户输入 "推荐资源"
       │
       ▼
  1. 薄弱点分析（不变）
       │
       ▼
  2. 双路检索
     ├── RAG 本地库 (ChromaDB local)
     └── 爬虫内容 (ChromaDB scraped, 新)
       │
       ▼
  3. 合并去重 → 按相关度排序 → Top 5
       │
       ▼
  4. LLM 合成推荐
     prompt = f"""
     学生薄弱课程: {weak_courses}
     本地学习资料: {local_results}
     网络最新资源: {scraped_results}
     
     请综合以上信息，生成个性化学习资源推荐，
     包含: 推荐理由、优先级排序、学习路径建议。
     """
       │
       ▼
  5. 输出结构化推荐 → 用户
```

### 3.3 resource_recommender.py 改动点

| 位置 | 改动 |
|------|------|
| `_call_llm` | 替换为 `llm_tools.llm_call(role="task")` |
| `_recommend_resources` | 增加 `search_scraped` 调用 |
| 模板回退 | 保留，作为 LLM + 双路检索均不可用时的兜底 |

---

## 四、项目缺陷报告（73 项全部清单）

### 🔴 严重（阻碍功能，需立即修复）

| # | 问题 | 位置 |
|---|------|------|
| 1 | LLM 全部硬编码 Anthropic，无法接入 DeepSeek | `llm_tools.py:132` 直接 `import anthropic` |
| 2 | `resource_recommender.py` 私有 `_call_llm` 绕过统一工具 | `resource_recommender.py:34-59` |
| 3 | `course_planner.py` 私有 `_call_llm` 重复实现 | `course_planner.py:34-64` |
| 4 | 6 处不同的 LLM 调用实现，违反 DRY | 两个私有 `_call_llm` + `llm_tools` 3 个方法 + `report_generator._generate_with_llm` |
| 5 | `data_manager.py` 调用 `upsert_user(has_major_timetable=1)` 但数据库中 `users` 表无此列 | `data_manager.py:115` → SQLite `OperationalError` |
| 6 | 报告唯一索引 `(user_id, report_type)` 导致生成同类型报告时静默覆盖历史 | `schema.sql:96` |
| 7 | `reports` 表唯一索引阻止保留多条报告历史 | `schema.sql:96` |

### 🟡 中等（影响质量，需尽快修复）

| # | 问题 | 位置 |
|---|------|------|
| 8 | 知识库仅 17 个文件，无真实题库/案例/外部链接 | `data/knowledge_base/` |
| 9 | 索引器 YAML 解析用简单 `partition(':')`，嵌套结构会出错 | `indexer.py:48` |
| 10 | ChromaDB 无增量更新，改文件后需全量重建 | `indexer.py` |
| 11 | `settings.py` ChromaDB 路径与实际不一致 | `settings.py:32` vs `indexer.py:23` |
| 12 | `EMBEDDING_MODEL` 环境变量未被使用 | `settings.py:35` vs `embeddings.py:59` |
| 13 | 无单元测试，94 个测试全为集成测试 | `test_phase*.py` |
| 14 | 测试无 Redis mock，无法验证 Redis 路径 | `test_phase3.py` 第五节 |
| 15 | Streamlit `user_id` 硬编码 `"u001"` | `streamlit_app.py:36` |
| 16 | `sqlite_models.py` Pydantic 模型未被使用 | `storage/sqlite_models.py` |
| 17 | `course_edit_tools.py` 从 agent 层导入，违反分层 | `course_edit_tools.py:14` |
| 18 | `requirements.txt` 缺少 `anthropic`, `streamlit`, `plotly`, `pandas` | `requirements.txt` |
| 19 | 无 `.gitignore` | 项目根目录 |
| 20 | 无 Docker/部署配置 | — |
| 21 | `streamlit_app.py` 切换对话时不重置 session_id | `streamlit_app.py:43` |
| 22 | `app.py` 多轮间 `has_minor` 有一轮延迟 | `app.py:207` |
| 23 | `AgentState.messages` 完全未被使用（死代码） | `state.py:56` |

### 🔵 轻微（体验改进，可延后）

| # | 问题 | 位置 |
|---|------|------|
| 24 | `progress_tracker.py` 课程代码正则与系统不一致（`{3}` vs `{3,4}`） | `progress_tracker.py:279` |
| 25 | 模型默认 `claude-sonnet-4-6`，应改为 provider-agnostic | `settings.py:38` |
| 26 | `report_generator.py` conflict JSON 无截断 | `report_generator.py:213` |
| 27 | 冲突检测 `dt_a.hour + 2 > 23` 溢出未处理 | `conflict_checker.py:260` |
| 28 | 无分页/加载更多 | `resource_recommender.py` |
| 29 | 无速率限制 | `app.py`, `streamlit_app.py` |
| 30 | 无多用户并发测试 | 所有测试用 `USER_ID = "u001"` |
| 31 | 无开发者文档/API 参考 | `docs/` |
| 32 | 无 CHANGELOG | — |
| 33 | 无 LLM 调用成功路径的测试（全走 fallback） | — |

---

### 优先级矩阵

```
            紧急度
            高          低
        ┌──────────┬──────────┐
重  高  │ 1 2 3 4  │ 8 9 10   │
要      │ 5 6 7    │ 11 12 13 │
性      ├──────────┼──────────┤
    低  │ 14 15 16 │ 24~33    │
        │ 17 18 19 │          │
        └──────────┴──────────┘
```

**首批必做（本文档范围）**：#1-7（LLM 接入 + 统一调用 + 报告索引修复 + data_manager bug）
**二批跟进**：#8-23（爬虫数据补充 + 测试覆盖 + 部署配置）
**持续优化**：#24-33（标准化、文档、体验）

---

## 五、实施路线

### Phase A: LLM 多供应商接入（优先级最高）

| 步骤 | 文件 | 工作量 |
|------|------|--------|
| A1 | `config/settings.py` — 增加 DeepSeek + Anthropic 双配置 | 0.5h |
| A2 | `src/tools/llm_tools.py` — 增加 `_call_deepseek` + provider 路由 + role 参数 | 2h |
| A3 | `resource_recommender.py` — 替换私有 `_call_llm` 为统一调用 | 0.5h |
| A4 | `course_planner.py` — 替换私有 `_call_llm` 为统一调用 | 0.5h |
| A5 | 测试 + 验证 DeepSeek API key 可用 | 1h |

### Phase B: 爬虫系统

| 步骤 | 文件 | 工作量 |
|------|------|--------|
| B1 | `src/scraper/__init__.py` + `targets.py` | 0.5h |
| B2 | `src/scraper/fetcher.py` — requests + UA + 重试 | 1h |
| B3 | `src/scraper/parser.py` — 中国大学MOOC + B站解析 | 2h |
| B4 | `src/scraper/pipelines.py` — 清洗 + 课程匹配 + 写 JSON | 1h |
| B5 | 首次爬取 + 数据入库 `data/scraped/` | 1h |

### Phase C: 资源推荐增强

| 步骤 | 文件 | 工作量 |
|------|------|--------|
| C1 | `src/tools/rag_tools.py` — 增加 `search_scraped` 方法 | 0.5h |
| C2 | `src/rag/indexer.py` — 增加 scraped collection 索引 | 1h |
| C3 | `resource_recommender.py` — 双路检索 + LLM 合成 | 2h |
| C4 | 端到端测试 | 1h |

### Phase D: 项目优化

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| D1 | 统一 LLM 调用入口（消灭所有私有 `_call_llm`） | 1h |
| D2 | Streamlit 多用户支持 | 1h |
| D3 | Pydantic 校验接入 | 1h |

### 预估总工作量

| Phase | 小时 |
|-------|------|
| A: LLM 接入 | 4.5h |
| B: 爬虫 | 5.5h |
| C: 推荐增强 | 4.5h |
| D: 优化 | 3h |
| **合计** | **17.5h** |

---

## 六、环境变量配置（新增）

```bash
# ── DeepSeek（主要）──
export DEEPSEEK_API_KEY="sk-xxxxxxxx"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"     # 默认
export DEEPSEEK_TASK_MODEL="deepseek-chat"               # 默认
export DEEPSEEK_REASONER_MODEL="deepseek-reasoner"       # 默认

# ── Anthropic（备用）──
export ANTHROPIC_API_KEY="sk-ant-..."                    # 可选
export ANTHROPIC_BASE_URL=""                             # 可选

# ── Provider 选择 ──
export LLM_PROVIDER="deepseek"                           # deepseek | anthropic | auto
```

---

## 七、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/settings.py` | 修改 | 拆分为 DEEPSEEK_CONFIG + ANTHROPIC_CONFIG |
| `src/tools/llm_tools.py` | 重写 | 多 provider + role 路由 + 降级链 |
| `src/agents/resource_recommender.py` | 修改 | 替换 _call_llm，增加双路检索 |
| `src/agents/course_planner.py` | 修改 | 替换私有 _call_llm |
| `src/scraper/__init__.py` | 新建 | 爬虫模块入口 |
| `src/scraper/fetcher.py` | 新建 | HTTP 请求封装 |
| `src/scraper/parser.py` | 新建 | HTML 解析器 |
| `src/scraper/pipelines.py` | 新建 | 数据清洗 + 存储 |
| `src/scraper/targets.py` | 新建 | 爬取目标配置 |
| `src/tools/rag_tools.py` | 修改 | 增加 search_scraped |
| `src/rag/indexer.py` | 修改 | 增加 scraped collection |
| `data/scraped/` | 新建 | 爬取数据存储目录 |
| `LLM接入设计文档_v2.md` | 重新生成 | 本文档的独立版本 |
| `test_phase5.py` | 新建 | Phase 5 测试 |
