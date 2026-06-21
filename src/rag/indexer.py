"""
知识库索引构建器。

扫描 knowledge_base/ 目录下的所有 Markdown 文件，
解析 YAML frontmatter + 正文，分块后 Embedding，存入 ChromaDB。
"""

import os
import re
import logging
import json
from typing import List, Dict, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

from config.settings import KNOWLEDGE_BASE_DIR
from src.rag.embeddings import get_embedding_model, BaseEmbedding

logger = logging.getLogger(__name__)

# ChromaDB 持久化路径
CHROMA_PERSIST_DIR = os.path.join(
    os.path.dirname(KNOWLEDGE_BASE_DIR), "embeddings", "chroma_db"
)

# 分块配置
CHUNK_SIZE = 400   # 每块约 400 字符
CHUNK_OVERLAP = 60  # 块间重叠 60 字符

# 简单的 YAML frontmatter 解析正则
FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

# 标题拆分正则
HEADING_RE = re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE)


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata_dict, body_text)。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    frontmatter_str = m.group(1)
    body = text[m.end():]

    meta = {}
    for line in frontmatter_str.strip().split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 尝试解析列表
            if value.startswith('[') and value.endswith(']'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            meta[key] = value
    return meta, body


def _split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE,
                       overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    将文本拆分为重叠的 chunks。

    优先在段落边界切分，避免切断句子。
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    # 先按段落分割
    paragraphs = text.split('\n\n')
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) < chunk_size:
            current += para + '\n\n'
        else:
            if current.strip():
                chunks.append(current.strip())
            # 如果本段本身超长，进一步切分
            if len(para) > chunk_size:
                # 按句子切分
                sentences = re.split(r'(?<=[。！？.!?])\s*', para)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) < chunk_size:
                        sub_chunk += sent
                    else:
                        if sub_chunk.strip():
                            chunks.append(sub_chunk.strip())
                        sub_chunk = sent
                if sub_chunk.strip():
                    current = sub_chunk + '\n\n'
                else:
                    current = ""
            else:
                current = para + '\n\n'

    if current.strip():
        chunks.append(current.strip())

    # 添加重叠
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i-1][-overlap:] if len(chunks[i-1]) > overlap else chunks[i-1]
            overlapped.append(prev_tail + '\n' + chunks[i])
        return overlapped

    return chunks


def build_index(knowledge_base_dir: str = None,
                embedding_model: BaseEmbedding = None,
                force_rebuild: bool = False) -> chromadb.Collection:
    """
    构建（或加载）ChromaDB 知识库索引。

    参数：
        knowledge_base_dir: 知识库根目录
        embedding_model: Embedding 模型
        force_rebuild: 是否强制重建索引

    返回：
        ChromaDB Collection 对象
    """
    if knowledge_base_dir is None:
        knowledge_base_dir = KNOWLEDGE_BASE_DIR
    if embedding_model is None:
        embedding_model = get_embedding_model()

    # 初始化 ChromaDB 客户端
    os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
    client = chromadb.PersistentClient(
        path=CHROMA_PERSIST_DIR,
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    collection_name = "study_agent_knowledge"

    # 检查是否已存在
    existing = client.list_collections()
    if not force_rebuild and any(c.name == collection_name for c in existing):
        logger.info(f"Loading existing ChromaDB collection: {collection_name}")
        return client.get_collection(collection_name)

    # 重建索引
    if any(c.name == collection_name for c in existing):
        logger.info(f"Deleting existing collection: {collection_name}")
        client.delete_collection(collection_name)

    collection = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # 扫描知识库文件
    md_files = []
    for root, dirs, files in os.walk(knowledge_base_dir):
        for f in files:
            if f.endswith('.md'):
                md_files.append(os.path.join(root, f))

    logger.info(f"Found {len(md_files)} markdown files to index")

    all_chunks = []
    all_metadatas = []
    all_ids = []

    for filepath in sorted(md_files):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        meta, body = _parse_frontmatter(content)
        chunks = _split_into_chunks(body)

        rel_path = os.path.relpath(filepath, knowledge_base_dir)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{rel_path.replace('/', '_').replace('.md', '')}_chunk{i}"
            all_ids.append(chunk_id)
            all_chunks.append(chunk)
            all_metadatas.append({
                "source": rel_path,
                "chunk_index": i,
                "total_chunks": len(chunks),
                **{k: str(v) for k, v in meta.items()},
            })

    logger.info(f"Created {len(all_chunks)} chunks from {len(md_files)} files")
    logger.info(f"Embedding with model: {embedding_model.name} (dim={embedding_model.dimension})")

    # 批量 Embedding + 写入
    batch_size = 32
    for i in range(0, len(all_chunks), batch_size):
        batch_chunks = all_chunks[i:i+batch_size]
        batch_ids = all_ids[i:i+batch_size]
        batch_meta = all_metadatas[i:i+batch_size]

        vectors = embedding_model.embed_batch(batch_chunks)

        collection.add(
            ids=batch_ids,
            embeddings=vectors,
            documents=batch_chunks,
            metadatas=batch_meta,
        )
        logger.debug(f"  Indexed {i+len(batch_chunks)}/{len(all_chunks)} chunks")

    logger.info(f"Index build complete. Collection: {collection_name}, "
                f"Chunks: {collection.count()}")
    return collection


def get_collection() -> chromadb.Collection:
    """获取已构建的 ChromaDB Collection（懒加载 + 自动构建）。"""
    return build_index()
