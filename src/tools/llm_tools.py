"""
统一 LLM 调用工具 — 多供应商支持。

Provider 优先级（由 LLM_PROVIDER 或自动检测控制）：
  1. DeepSeek  (OpenAI 兼容接口) — 主要
  2. Anthropic                       — 备用
  3. 模板回退                         — 兜底

模型角色:
  role="task"     → deepseek-chat       (报告/推荐/问答)
  role="reasoner" → deepseek-reasoner   (复杂推理/风险分析)
  role="chat"     → deepseek-chat       (闲聊，使用轻松 system prompt)
"""

import logging
import time
import re
from typing import Optional, Callable

from config.settings import DEEPSEEK_CONFIG, ANTHROPIC_CONFIG, LLM_PROVIDER

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  异常分类
# ══════════════════════════════════════════════════════

class LLMError(Exception):
    """LLM 调用异常基类。"""


class LLMKeyMissing(LLMError):
    """API key 未配置。"""


class LLMNetworkError(LLMError):
    """网络不可达（DNS / 连接超时 / 连接重置）。"""


class LLMAPIRateLimit(LLMError):
    """API 速率限制（429）。"""


class LLMAPIError(LLMError):
    """API 返回错误（4xx / 5xx）。"""


def _classify_error(exception: Exception) -> LLMError:
    """将原始异常归类为 LLM 错误类型。"""
    msg = str(exception).lower()

    # 检查是否有 status_code 属性（openai / anthropic / httpx）
    status = getattr(exception, "status_code", 0)

    if status == 429:
        return LLMAPIRateLimit(f"API rate limited (429): {msg[:100]}")
    if status in (401, 403):
        return LLMKeyMissing(f"API auth failed ({status}): {msg[:100]}")
    if 400 <= status < 500:
        return LLMAPIError(f"API client error ({status}): {msg[:100]}")
    if 500 <= status < 600:
        return LLMAPIError(f"API server error ({status}): {msg[:100]}")

    # 网络层关键词
    if any(kw in msg for kw in ["timeout", "timed out", "connect", "resolve",
                                  "name or service not known", "getaddrinfo",
                                  "connection refused", "connection reset"]):
        return LLMNetworkError(f"Network error: {msg[:100]}")

    return LLMError(f"Unknown LLM error: {msg[:100]}")


# ══════════════════════════════════════════════════════
#  Provider 调用函数
# ══════════════════════════════════════════════════════

def _call_deepseek(prompt: str, system: str, model: str,
                   max_tokens: int, temperature: float) -> str:
    """DeepSeek API 调用（OpenAI 兼容接口）。"""
    from openai import OpenAI

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
    usage = response.usage
    logger.info("DeepSeek OK | model=%s tokens_in=%s tokens_out=%s",
                 model,
                 getattr(usage, "prompt_tokens", "?"),
                 getattr(usage, "completion_tokens", "?"))
    return response.choices[0].message.content


def _call_anthropic(prompt: str, system: str, model: str,
                    max_tokens: int, temperature: float) -> str:
    """Anthropic API 调用（备用）。"""
    import anthropic

    kwargs = {"api_key": ANTHROPIC_CONFIG["api_key"]}
    if ANTHROPIC_CONFIG["base_url"]:
        kwargs["base_url"] = ANTHROPIC_CONFIG["base_url"]

    client = anthropic.Anthropic(**kwargs)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    logger.info("Anthropic OK | model=%s tokens_in=%s tokens_out=%s",
                 model,
                 getattr(message.usage, "input_tokens", "?"),
                 getattr(message.usage, "output_tokens", "?"))
    return message.content[0].text


# ══════════════════════════════════════════════════════
#  Provider 自动选择
# ══════════════════════════════════════════════════════

def _resolve_provider() -> str:
    """
    根据 LLM_PROVIDER 和可用 API key 决定使用的 provider。

    返回: "deepseek" | "anthropic" | "none"
    """
    if LLM_PROVIDER == "deepseek":
        return "deepseek" if DEEPSEEK_CONFIG["api_key"] else "none"
    if LLM_PROVIDER == "anthropic":
        return "anthropic" if ANTHROPIC_CONFIG["api_key"] else "none"
    if LLM_PROVIDER == "auto":
        if DEEPSEEK_CONFIG["api_key"]:
            return "deepseek"
        if ANTHROPIC_CONFIG["api_key"]:
            return "anthropic"
        return "none"
    return "none"


def _select_model(role: str) -> tuple:
    """
    根据调用角色选择 (provider, model, max_tokens)。

    role:
      "task"     → deepseek-chat       (报告/推荐/问答)
      "reasoner" → deepseek-reasoner   (复杂推理/风险分析)
      "chat"     → deepseek-chat       (闲聊)
    """
    provider = _resolve_provider()

    if provider == "deepseek":
        if role == "reasoner":
            return ("deepseek", DEEPSEEK_CONFIG["reasoner_model"], 4096)
        return ("deepseek", DEEPSEEK_CONFIG["task_model"], 4096)

    if provider == "anthropic":
        return ("anthropic", ANTHROPIC_CONFIG["model"], ANTHROPIC_CONFIG["max_tokens"])

    return ("none", "", 0)


# ══════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════

def llm_call(prompt: str,
             system: str = "",
             fallback: Callable[[], str] = None,
             role: str = "task",
             model: str = None,
             max_tokens: int = None,
             temperature: float = None) -> str:
    """
    统一 LLM 调用入口（多供应商）。

    参数:
        prompt:   用户消息（必填）
        system:   系统提示词
        fallback: 回退函数，无参数返回 str。不提供时返回 "[FALLBACK]"
        role:     模型角色 — "task" | "reasoner" | "chat"
        model:    覆盖默认模型
        max_tokens: 覆盖默认 max_tokens
        temperature: 覆盖默认 temperature

    返回:
        LLM 回复文本，或 fallback 函数的返回值。

    降级链: DeepSeek → Anthropic → fallback()
    """
    t_start = time.time()
    prompt_preview = prompt[:60].replace("\n", " ")
    logger.info("LLM call start | role=%s prompt='%s...'", role, prompt_preview)

    # ── 获取 provider ──
    provider, default_model, default_tokens = _select_model(role)
    model = model or default_model
    max_tokens = max_tokens or default_tokens
    temperature = temperature if temperature is not None else 0.3

    # ── L1: DeepSeek ──
    if provider == "deepseek":
        try:
            result = _call_deepseek(prompt, system, model, max_tokens, temperature)
            elapsed = time.time() - t_start
            logger.info("LLM call OK | provider=DeepSeek elapsed=%.2fs", elapsed)
            return result
        except Exception as e:
            elapsed = time.time() - t_start
            classified = _classify_error(e)
            logger.warning("DeepSeek failed after %.2fs: %s", elapsed, classified)
            # 继续尝试 Anthropic

    # ── L2: Anthropic ──
    if ANTHROPIC_CONFIG["api_key"]:
        try:
            result = _call_anthropic(
                prompt, system,
                ANTHROPIC_CONFIG["model"],
                ANTHROPIC_CONFIG["max_tokens"],
                temperature,
            )
            elapsed = time.time() - t_start
            logger.info("LLM call OK | provider=Anthropic (fallback) elapsed=%.2fs", elapsed)
            return result
        except Exception as e:
            elapsed = time.time() - t_start
            classified = _classify_error(e)
            logger.warning("Anthropic failed after %.2fs: %s", elapsed, classified)

    # ── L3: Fallback ──
    elapsed = time.time() - t_start
    logger.info("LLM fallback: all providers unavailable (%.2fs)", elapsed)
    return _do_fallback(fallback, "all_providers_unavailable")


# ══════════════════════════════════════════════════════
#  便捷方法
# ══════════════════════════════════════════════════════

def llm_call_with_template(prompt: str,
                           system: str = "",
                           template: str = "",
                           role: str = "task") -> str:
    """LLM 调用 + 静态模板回退。"""
    def _template():
        return template
    return llm_call(prompt=prompt, system=system, fallback=_template, role=role)


def llm_call_json(prompt: str,
                  system: str = "",
                  fallback_json: dict = None,
                  role: str = "task") -> dict:
    """LLM 调用，期望返回 JSON。"""
    fallback_data = fallback_json or {}

    def _json_fallback():
        return None

    raw = llm_call(prompt=prompt, system=system, fallback=_json_fallback, role=role)
    if raw is None:
        return fallback_data

    # 尝试提取 JSON 块
    json_match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
    if json_match:
        try:
            import json
            return json.loads(json_match.group(1))
        except Exception:
            pass

    # 尝试直接解析
    try:
        import json
        return json.loads(raw)
    except Exception:
        pass

    logger.warning("LLM JSON parse failed, using fallback")
    return fallback_data


def llm_chat(prompt: str, system: str = "", fallback: Callable[[], str] = None) -> str:
    """闲聊专用 — 使用轻松友好的 system prompt。"""
    chat_system = system or (
        "你是一位友好的大学学业助手，叫「学业小帮手」。"
        "用轻松自然的语气与同学交流，像朋友聊天一样。"
        "可以适当使用 emoji，但不要过度。回答简短，2-3 句话即可。"
    )
    return llm_call(prompt, system=chat_system, fallback=fallback, role="chat")


def llm_reasoner(prompt: str, system: str = "",
                 fallback: Callable[[], str] = None) -> str:
    """复杂推理专用 — 使用 deepseek-reasoner。"""
    return llm_call(prompt, system=system, fallback=fallback, role="reasoner")


def llm_is_available() -> bool:
    """检查是否有任何 LLM provider 可用。"""
    return bool(DEEPSEEK_CONFIG["api_key"] or ANTHROPIC_CONFIG["api_key"])


# ══════════════════════════════════════════════════════
#  内部辅助
# ══════════════════════════════════════════════════════

def _do_fallback(fallback: Optional[Callable[[], str]],
                 reason: str) -> str:
    """执行回退逻辑。"""
    if fallback:
        try:
            return fallback()
        except Exception as e:
            logger.error("Fallback function itself failed: %s", e)
    return f"[LLM unavailable: {reason}]"
