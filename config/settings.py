"""
全局配置模块。

使用方式:
    from config.settings import DEEPSEEK_CONFIG, ANTHROPIC_CONFIG, ...
"""

import os

# ── .env 文件加载 ─────────────────────────────────────

def _load_env_file() -> None:
    """加载项目根目录 .env 文件（不覆盖已设置的环境变量）。"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            if val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val

_load_env_file()

# ── 项目路径 ─────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Redis ───────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL = 30 * 60           # 30 分钟
RAG_CACHE_TTL = 1 * 60 * 60     # 1 小时

# ── SQLite ──────────────────────────────────────────
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "database")
SQLITE_PATH = os.getenv("SQLITE_PATH", os.path.join(DATA_DIR, "study_agent.db"))
SCHEMA_PATH = os.path.join(DATA_DIR, "schema.sql")
SEED_PATH = os.path.join(DATA_DIR, "seed_data.sql")

# ── 知识库 ──────────────────────────────────────────
KNOWLEDGE_BASE_DIR = os.path.join(PROJECT_ROOT, "data", "knowledge_base")
CHROMA_PERSIST_DIR = os.path.join(PROJECT_ROOT, "data", "knowledge_base", "embeddings", "chroma_db")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

# ── 爬虫数据 ────────────────────────────────────────
SCRAPED_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "scraped")

# ── LLM Provider ────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")

# DeepSeek（主要，OpenAI 兼容接口）
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

# 兼容旧代码的 LLM_CONFIG 引用（不推荐新代码使用）
LLM_CONFIG = {
    "model": ANTHROPIC_CONFIG["model"],
    "api_key": ANTHROPIC_CONFIG["api_key"],
    "api_base": ANTHROPIC_CONFIG["base_url"],
    "max_tokens": 2048,
    "temperature": 0.3,
}

# ── 日志 ────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
