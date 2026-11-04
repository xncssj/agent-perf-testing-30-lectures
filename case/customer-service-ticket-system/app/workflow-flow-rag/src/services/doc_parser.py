"""文档解析与分块：支持 PDF、Markdown、TXT 格式。"""
from __future__ import annotations

import re
from pathlib import Path


def parse_document(file_path: str, filename: str) -> str:
    """根据文件扩展名解析文档内容。"""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return _parse_pdf(file_path)
    elif suffix in (".md", ".markdown"):
        return _parse_markdown(file_path)
    elif suffix in (".txt", ".text"):
        return _parse_text(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")


def _parse_pdf(file_path: str) -> str:
    """解析 PDF 文件。"""
    try:
        import PyPDF2
    except ImportError:
        raise ImportError("需要安装 PyPDF2: pip install PyPDF2")

    text_parts: list[str] = []
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def _parse_markdown(file_path: str) -> str:
    """解析 Markdown 文件，提取纯文本。"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    # 移除代码块
    content = re.sub(r"```[\s\S]*?```", "", content)
    # 移除图片
    content = re.sub(r"!\[.*?\]\(.*?\)", "", content)
    # 保留链接文本
    content = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content)
    # 移除 HTML 标签
    content = re.sub(r"<[^>]+>", "", content)
    return content.strip()


def _parse_text(file_path: str) -> str:
    """解析纯文本文件。"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """将文本分块。

    策略：
    1. 优先按段落（双换行）分块
    2. 超过 chunk_size 的段落再按句子拆分
    3. 相邻块之间保留 overlap 字符的重叠
    """
    if not text.strip():
        return []

    # 先按段落分割
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 如果当前块加上新段落不超过限制，合并
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}" if current else para
        else:
            # 保存当前块
            if current:
                chunks.append(current)
            # 如果单个段落超过限制，按句子拆分
            if len(para) > chunk_size:
                sub_chunks = _split_long_paragraph(para, chunk_size, overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    # 添加重叠
    if overlap > 0 and len(chunks) > 1:
        chunks = _add_overlap(chunks, overlap)

    return chunks


def _split_long_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    """拆分超长段落。"""
    # 按句子分割（中文句号、英文句号、感叹号、问号）
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 <= chunk_size:
            current = f"{current} {sent}" if current else sent
        else:
            if current:
                chunks.append(current)
            current = sent

    if current:
        chunks.append(current)

    return chunks


def _add_overlap(chunks: list[str], overlap: int) -> list[str]:
    """为相邻块添加重叠。"""
    if len(chunks) <= 1:
        return chunks

    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        # 取前一个块的末尾 overlap 字符
        overlap_text = prev[-overlap:] if len(prev) > overlap else prev
        # 找到一个自然断点（空格或标点）
        space_idx = overlap_text.find(" ")
        if space_idx > 0:
            overlap_text = overlap_text[space_idx + 1:]
        combined = f"{overlap_text}\n\n{chunks[i]}"
        result.append(combined)

    return result
