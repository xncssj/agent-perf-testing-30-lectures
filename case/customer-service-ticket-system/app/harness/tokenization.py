"""确定性 token 边界（Mock 流式与用量口径的唯一事实源）。

Mock 模型是确定性模拟器，此前按**单个字符**产出流式片段，却把每个片段当作
token 使用，导致采集脚本算出的 "TPOT/ITL" 实为相邻字符事件间隔（字符串粒度），
不能代表 token 间隔（2026-09-12 待办 2a）。本模块给出一个确定性的 token 边界，
让"一个流式片段 = 一个 token"在 Mock 口径下成立，从而 TPOT/ITL 可按 token 计。

边界规则（确定性、可复算，用于模拟而非真实分词器）：

- 连续汉字按最多 2 字成组（常见中文 BPE 的近似：约 1.5–2 字/token）；
- 连续 ASCII 字母或数字成组为一个 token（词/单号）；
- 其他字符（空白、标点、符号）各自成一个 token。

该边界只用于 Mock 口径的节奏与用量；接入真实模型后用量与间隔取端点实际返回，
不再使用本模块。
"""
from __future__ import annotations

_CJK_RANGES = (
    (0x3400, 0x4DBF),   # 扩展 A
    (0x4E00, 0x9FFF),   # 基本区
    (0xF900, 0xFAFF),   # 兼容表意
)

CJK_PER_TOKEN = 2


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """把文本切成 token 列表（确定性；空文本返回空列表）。"""
    tokens: list[str] = []
    buf = ""
    for ch in text or "":
        if is_cjk(ch):
            buf += ch
            if len(buf) >= CJK_PER_TOKEN:
                tokens.append(buf)
                buf = ""
        elif ch.isascii() and ch.isalnum():
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    return tokens


def token_count(text: str) -> int:
    """token 数（Mock 用量口径：completion/prompt token 由此得出，替代字符数估算）。"""
    return len(tokenize(text))
