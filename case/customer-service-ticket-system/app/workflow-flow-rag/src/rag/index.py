"""轻量 RAG 索引：中文分词、倒排、BM25 与规则重排。

无外部向量库依赖，进程内构建确定性索引（RAG 索引设计说明 的最小实现）：
- token = 英文/数字词 + 中文 bigram（连续汉字滑动二元组）
- bm25(k1=1.2, b=0.75) 打分 + 命中率 + 类别规则加权
- doc_id 与 DataStore.doc_id 对齐（category:title），供质量测试断言
"""
from __future__ import annotations

import math
import re
from collections import Counter

_ALNUM_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for m in _CJK_RE.finditer(text):
        seg = m.group()
        tokens.extend(seg[i : i + 2] for i in range(max(1, len(seg) - 1)))
    tokens.extend(_ALNUM_RE.findall(text))
    return tokens


def _doc_len(freqs: dict[str, int]) -> int:
    return sum(freqs.values())


class RagIndex:
    """文档倒排索引。"""

    # 窄范围政策意图触发词（课程知识检索契约）：
    # 问题同时含「政策/规定」与「退货/退款/换货/售后」之一，且未指定
    # 知识库中的商品标识时，有词项命中的售后政策文档排在流程 FAQ 前。
    # 规则只处理这一明确边界，不宣称解决所有语义检索问题。
    _POLICY_TOPIC_WORDS = ("政策", "规定")
    _POLICY_DOMAIN_WORDS = ("退货", "退款", "换货", "售后")

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self._postings: dict[str, Counter] = {}
        self._doc_tokens: dict[str, Counter] = {}
        self.avgdl = 0.0
        # 商品标识 token（来自 products 文档标题，如 iphone/airpods/pro）
        self._product_tokens: set[str] = set()

    def build(self, docs: list[dict]) -> None:
        self.docs = {d["doc_id"]: d for d in docs}
        self._postings = {}
        self._doc_tokens = {}
        self._product_tokens = set()
        total_len = 0
        for d in docs:
            text = " ".join([d.get("title", ""), (d.get("content") or "")])
            freqs = Counter(_tokenize(text))
            self._doc_tokens[d["doc_id"]] = freqs
            total_len += _doc_len(freqs)
            for tok, cnt in freqs.items():
                self._postings.setdefault(tok, Counter())[d["doc_id"]] = cnt
            if d.get("category") == "products":
                self._product_tokens.update(
                    t for t in _ALNUM_RE.findall((d.get("title") or "").lower())
                    if len(t) >= 2 and not t.isdigit())
        self.avgdl = total_len / len(docs) if docs else 0.0

    def _idf(self, tok: str, n: int) -> float:
        df = len(self._postings.get(tok, {}))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        qfreqs = Counter(_tokenize(query))
        if not qfreqs:
            return []
        n = len(self.docs)
        scores: dict[str, float] = {}
        ratio: dict[str, float] = {}
        for tok, qcnt in qfreqs.items():
            post = self._postings.get(tok, {})
            for doc_id, doc_cnt in post.items():
                idf = self._idf(tok, n)
                dl = _doc_len(self._doc_tokens[doc_id])
                bm25 = idf * (doc_cnt * 1.2) / (doc_cnt + 1.2 * (0.25 + 0.75 * dl / self.avgdl))
                scores[doc_id] = scores.get(doc_id, 0.0) + bm25 * qcnt
                doc_toks = self._doc_tokens[doc_id]
                hits = sum(min(doc_toks[t], qq) for t in qfreqs for qq in [qfreqs[t]])
                ratio[doc_id] = hits / max(1, _doc_len(doc_toks))
        # 规则重排：类别命中加 0.2，标题先验加 0.1
        boost = self._rule_boost(query)
        ranked = []
        for doc_id, score in scores.items():
            final = score + 0.6 * ratio.get(doc_id, 0.0) + boost.get(doc_id, 0.0)
            ranked.append((final, doc_id))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        ranked = self._apply_policy_rerank(query, ranked)
        return [
            {"doc_id": doc_id, "title": d["title"], "category": d.get("category", ""),
             "content": (d.get("content") or ""), "score": round(score, 4)}
            for score, doc_id in ranked[:top_k]
            for d in [self.docs[doc_id]]
        ]

    def _apply_policy_rerank(self, query: str,
                             ranked: list[tuple[float, str]]) -> list[tuple[float, str]]:
        """窄范围政策意图重排：有词项命中的售后政策文档排在流程 FAQ 前。

        仅当问题含「政策/规定」+「退货/退款/换货/售后」之一且未指定
        知识库内商品标识时触发；命中的政策文档提到最前（组内保持
        BM25+规则分序），其余文档相对顺序不变。申请步骤问题与指定
        商品的商品文档优先级不受影响。
        """
        if not self._policy_intent(query):
            return ranked
        policy_hits = [r for r in ranked
                       if self.docs[r[1]].get("category") == "policies"]
        if not policy_hits:
            return ranked
        others = [r for r in ranked
                  if self.docs[r[1]].get("category") != "policies"]
        return policy_hits + others

    def _policy_intent(self, query: str) -> bool:
        """政策意图判定：政策词 + 售后域词，且未指定知识库内商品标识。"""
        if not any(w in query for w in self._POLICY_TOPIC_WORDS):
            return False
        if not any(w in query for w in self._POLICY_DOMAIN_WORDS):
            return False
        q_toks = set(_ALNUM_RE.findall(query.lower()))
        if q_toks & self._product_tokens:
            return False
        return True

    def _rule_boost(self, query: str) -> dict[str, float]:
        """标题先验加权：查询 token 与标题 token 重叠越多权重越高。

        覆盖“问题词落标题、但正文与竞品文档重叠大”的排序场景
        （如“我要退货”/<policies:售后服务政策> 与 <faq:如何申请退货> 的区分）。
        """
        toks = set(_tokenize(query))
        if not toks:
            return {}
        boost: dict[str, float] = {}
        for doc_id, doc in self.docs.items():
            title_toks = set(_tokenize(doc.get("title") or ""))
            inter = len(toks & title_toks)
            if inter:
                boost[doc_id] = boost.get(doc_id, 0.0) + 0.3 * inter
        return boost


def build_index(docs: list[dict]) -> RagIndex:
    idx = RagIndex()
    idx.build(docs)
    return idx