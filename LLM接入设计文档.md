# LLM 接入设计文档

> 版本: 1.0 | 日期: 2026-06-11 | 关联项目: 大学生辅修学习规划与跟踪多智能体系统

---

## 一、概述

### 1.1 定位

系统以**规则引擎为主、LLM 为辅**。所有 Agent 首先具备完整的规则/模板回退能力，LLM 作为可选增强层，仅在 API key 配置且网络可达时启用。

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **离线可用** | API key 为空时，系统 100% 正常运行（规则模板），不依赖外部服务 |
| **优雅降级** | 网络异常/接口报错时，自动切换模板，不抛异常、不中断用户交互 |
| **按需接入** | 仅报告生成、兜底问答使用统一 LLM 工具；其余模块保留原有规则逻辑 |
| **日志透明** | 每次调用记录输入摘要、耗时、状态、回退原因，方便排查 |

---

## 二、架构

```
┌──────────────────────────────────────────────────────┐
│                    调用方 Agent                       │
│                                                      │
│  报告生成 Agent  ──────────────┐                      │
│  兜底问答 Handler ────────────┤                      │
│                               │                      │
│  (其余 Agent 保留原有规则逻辑)  │                      │
│                               ▼                      │
│                    ┌──────────────────┐              │
│                    │  llm_tools.py    │              │
│                    │  (统一调用入口)    │              │
│                    └────────┬─────────┘              │
│                             │                        │
│            ┌────────────────┼────────────────┐       │
│            │                │                │       │
│     ┌──────▼──────┐  ┌─────▼─────┐  ┌──────▼──────┐│
│     │ 密钥检测     │  │ 网络检测   │  │ 接口报错    ││
│     │ API key为空? │  │ DNS/超时?  │  │ 4xx/5xx?   ││
│     └──────┬──────┘  └─────┬─────┘  └──────┬──────┘│
│            │               │               │        │
│            └───────────────┼───────────────┘        │
│                            │                        │
│                     ┌──────▼──────┐                 │
│                     │  降级处理    │                 │
│                     │  fallback() │                 │
│                     └─────────────┘                 │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │              Anthropic API                   │    │
│  │         (仅在密钥有效+网络可达时)             │    │
│  └─────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

---

## 三、统一调用接口

### 3.1 核心函数

```python
from src.tools.llm_tools import llm_call

result = llm_call(
    prompt="请生成一份学业报告...",
    system="你是一位大学学业规划顾问。",
    fallback=lambda: "【报告模板回退】\n...",   # 回退函数
    model=None,        # 默认取 LLM_CONFIG.model
    max_tokens=None,   # 默认取 LLM_CONFIG.max_tokens
    temperature=None,  # 默认取 LLM_CONFIG.temperature
)
```

### 3.2 便捷方法

| 函数 | 用途 |
|------|------|
| `llm_call(prompt, system, fallback)` | 通用调用，自定义回退函数 |
| `llm_call_with_template(prompt, system, template)` | 调用 + 静态文本模板回退 |
| `llm_call_json(prompt, system, fallback_json)` | 调用 + 自动提取 JSON + dict 回退 |
| `llm_is_available()` | 检查 API key 是否已配置 |

### 3.3 异常类型

| 异常类 | 触发条件 | 处理方式 |
|--------|---------|---------|
| `LLMKeyMissing` | API key 为空或鉴权失败 (401/403) | 直接走 fallback，不发起网络请求 |
| `LLMNetworkError` | DNS 失败、连接超时、连接重置 | 走 fallback，记录网络状态 |
| `LLMAPIRateLimit` | 429 Too Many Requests | 走 fallback，不重试 |
| `LLMAPIError` | 其他 4xx/5xx | 走 fallback，记录错误详情 |

---

## 四、降级策略

### 4.1 四级降级链

```
Level 1: 密钥检测
  API key 为空 → 立即返回 fallback，不发起任何网络请求
  耗时: <1ms

Level 2: 网络异常
  DNS 解析失败 / TCP 连接超时 / 连接重置
  → 捕获异常 → _classify_error() → 返回 fallback
  耗时: ~3-5s（取决于 TCP 超时设置）

Level 3: 接口报错
  4xx (客户端错误) / 5xx (服务端错误)
  → 捕获异常 → _classify_error() → 返回 fallback
  耗时: ~1-3s

Level 4: 回退函数异常
  fallback 函数自身出错
  → 返回 "[LLM unavailable: {reason}]"
  耗时: <1ms
```

### 4.2 日志规范

每次调用输出两条日志（正常）或三条（降级）：

```
# 正常
LLM call start | model=claude-sonnet-4-6 prompt='请生成一份学业报告...'
LLM call OK | model=claude-sonnet-4-6 tokens_in=450 tokens_out=1200 elapsed=2.35s

# 降级（API key 缺失）
LLM call start | model=claude-sonnet-4-6 prompt='今天天气怎么样...'
LLM fallback: api_key_missing (0.00s)

# 降级（网络超时）
LLM call start | model=claude-sonnet-4-6 prompt='解释一下CAPM模型...'
LLM fallback: LLMNetworkError after 3.21s | Network error: connection timed out

# 降级（速率限制）
LLM call start | model=claude-sonnet-4-6 prompt='生成综合报告...'
LLM fallback: LLMAPIRateLimit after 1.05s | API rate limited (429)
```

---

## 五、接入清单

### 5.1 Phase 4 Agent 接入

| Agent | 接入方式 | 回退策略 |
|-------|---------|---------|
| **报告生成 Agent** | `llm_call(prompt, system, fallback=template_func)` | 规则模板填充（已有 stats_tools 数据） |
| **兜底问答 Handler** | `llm_call(prompt, system, fallback=friendly_msg)` | 预设回复列表 + 引导到可用功能 |

### 5.2 现有 Agent 状态

| Agent | LLM 使用 | 说明 |
|-------|---------|------|
| CoursePlanner | ✅ 已有 `_call_llm`（模块内私有） | **保留不变**，不做迁移 |
| ResourceRecommender | ✅ 已有 `_call_llm`（模块内私有） | **保留不变**，不做迁移 |
| ProgressTracker | ❌ 不涉及 LLM | 纯 SQLite 查询 + 统计 |
| ConflictChecker | ❌ 不涉及 LLM | 纯算法（时间重叠） |
| Router | ❌ 不涉及 LLM | 关键词 + 正则评分 |

> 现有 Agent 的 `_call_llm` 函数功能完整（含 Anthropic API 调用 + 回退），
> 考虑到稳定性和回归风险，**不对已有模块做强制迁移**。

---

## 六、配置

### 6.1 环境变量

```bash
# 必填（启用 LLM 增强）
export ANTHROPIC_API_KEY="sk-ant-..."

# 可选（自定义 API 地址，如代理/镜像）
export ANTHROPIC_BASE_URL="https://api.anthropic.com"

# 可选（模型选择）
export LLM_MODEL="claude-sonnet-4-6"
```

### 6.2 settings.py 对应配置

```python
LLM_CONFIG = {
    "model":       os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
    "api_key":     os.getenv("ANTHROPIC_API_KEY", ""),
    "api_base":    os.getenv("ANTHROPIC_BASE_URL", ""),
    "max_tokens":  2048,
    "temperature": 0.3,
}
```

### 6.3 无 API Key 运行

不设置 `ANTHROPIC_API_KEY` 时：
- 课程规划：回退规则模板（3 个辅修方案基于 RAG 结果排序）
- 资源推荐：回退模板（薄弱点 → 学习计划步骤文本）
- 报告生成（Phase 4）：回退 Markdown 模板（stats_tools 数据填充）
- 兜底问答（Phase 4）：回退预设回复列表

**用户体验无中断，所有功能正常工作。**

---

## 七、文件清单

| 文件 | 说明 |
|------|------|
| `src/tools/llm_tools.py` | 统一 LLM 调用工具（新增） |
| `config/settings.py` | LLM_CONFIG 配置（已有） |
| `src/agents/course_planner.py` | `_call_llm()` 私有函数（保留不变） |
| `src/agents/resource_recommender.py` | `_call_llm()` 私有函数（保留不变） |
| `src/agents/report_generator.py` | Phase 4 新建，使用 `llm_tools.llm_call()` |
| `src/agents/chat_qa.py` | Phase 4 新建，使用 `llm_tools.llm_call()` |

---

## 八、扩展性

### 8.1 切换 LLM 提供商

如需从 Anthropic 切换到 OpenAI / 国内模型：

1. 在 `llm_tools.py` 中新增一个 `_call_openai()` 或 `_call_qwen()` 内部函数
2. 在 `llm_call()` 中根据 `LLM_CONFIG["provider"]` 分发
3. 错误分类函数 `_classify_error()` 扩展对应异常类型

### 8.2 增加重试策略

当前版本不做自动重试（避免用户等待）。如需添加：

```python
# 在 llm_call() 中 LLMAPIRateLimit 分支
if isinstance(classified, LLMAPIRateLimit) and retries < 3:
    time.sleep(2 ** retries)
    return llm_call(prompt, system, fallback, model,
                    max_tokens, temperature, _retries=retries+1)
```

### 8.3 流式输出

当前版本返回完整文本。Phase 5 Streamlit 前端可扩展为：

```python
def llm_call_stream(prompt, system, model):
    with client.messages.stream(...) as stream:
        for chunk in stream:
            yield chunk
```
