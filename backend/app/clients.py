"""外部服务客户端：DeepSeek 大模型、BGE 向量、Tavily 检索。

所有调用均带超时与重试，避免阻塞；向量计算用 numpy 余弦相似度。
"""
from __future__ import annotations
import json
import time
import hashlib
from typing import Any
import httpx
import numpy as np
from openai import OpenAI
from .config import settings

# ---------------- DeepSeek 大模型 ----------------
_llm = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=120)


def chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = False,
         max_tokens: int = 2048, retries: int = 2) -> str:
    """调用 DeepSeek 对话补全。json_mode=True 时强制返回 JSON。"""
    kwargs: dict[str, Any] = dict(model=settings.deepseek_model, messages=messages,
                                  temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = _llm.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"DeepSeek 调用失败: {last_err}")


def chat_json(messages: list[dict], temperature: float = 0.1, max_tokens: int = 2048) -> dict:
    """返回解析后的 JSON 对象，带容错。"""
    raw = chat(messages, temperature=temperature, json_mode=True, max_tokens=max_tokens)
    return _safe_json(raw)


def chat_stream(messages: list[dict], temperature: float = 0.5, max_tokens: int = 1024):
    """流式对话补全，逐 token yield 文本增量（用于 AI 助手打字机效果）。"""
    stream = _llm.chat.completions.create(
        model=settings.deepseek_model, messages=messages,
        temperature=temperature, max_tokens=max_tokens, stream=True)
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except (IndexError, AttributeError):
            delta = None
        if delta:
            yield delta


def _safe_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试截取首个 { ... }
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


# ---------------- BGE 向量嵌入 ----------------
_embed_client = httpx.Client(timeout=30)
_embed_cache: dict[str, list[float]] = {}


def embed(text: str) -> list[float]:
    """单文本向量，带本地缓存。"""
    key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if key in _embed_cache:
        return _embed_cache[key]
    vecs = embed_batch([text])
    return vecs[0] if vecs else []


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量。失败时返回零向量，保证不阻断主流程。"""
    if not texts:
        return []
    try:
        r = _embed_client.post(
            f"{settings.embed_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.embed_api_key}",
                     "Content-Type": "application/json"},
            json={"model": settings.embed_model, "input": texts},
        )
        r.raise_for_status()
        data = r.json()["data"]
        out = [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
        for t, v in zip(texts, out):
            _embed_cache[hashlib.md5(t.encode("utf-8")).hexdigest()] = v
        return out
    except Exception:  # noqa: BLE001
        return [[0.0] * settings.embed_dim for _ in texts]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度，安全处理零向量。"""
    if not a or not b:
        return 0.0
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


# ---------------- Tavily 联网检索 ----------------
_tavily = httpx.Client(timeout=40)


def tavily_search(query: str, max_results: int = 5, days: int | None = None) -> list[dict]:
    """联网检索，用于能力项交叉验证与新岗位证据采集。"""
    if not settings.tavily_api_key:
        return []
    payload: dict[str, Any] = {"query": query, "max_results": max_results,
                               "search_depth": "advanced"}
    if days:
        payload["days"] = days
        payload["topic"] = "news"
    try:
        r = _tavily.post("https://api.tavily.com/search",
                         headers={"Authorization": f"Bearer {settings.tavily_api_key}",
                                  "Content-Type": "application/json"},
                         json=payload)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:  # noqa: BLE001
        return []


# ---------------- Serper.dev (Google) 检索 ----------------
_serper = httpx.Client(timeout=40)


def serper_search(query: str, max_results: int = 5) -> list[dict]:
    """Google 检索（独立于 Tavily 的第二来源），归一化为 {title,url,content}。"""
    if not settings.serper_api_key:
        return []
    try:
        r = _serper.post("https://google.serper.dev/search",
                         headers={"X-API-KEY": settings.serper_api_key,
                                  "Content-Type": "application/json"},
                         json={"q": query, "num": max_results, "gl": "cn", "hl": "zh-cn"})
        r.raise_for_status()
        data = r.json()
        out = []
        for it in data.get("organic", [])[:max_results]:
            out.append({"title": it.get("title", ""), "url": it.get("link", ""),
                        "content": it.get("snippet", "")})
        return out
    except Exception:  # noqa: BLE001
        return []


def multi_source_search(query: str, max_results: int = 5) -> list[dict]:
    """多源检索：Tavily + Serper 合并去重，标注来源（用于交叉验证）。"""
    results = []
    for r in tavily_search(query, max_results=max_results):
        results.append({"title": r.get("title", ""), "url": r.get("url", ""),
                        "content": (r.get("content") or "")[:600], "provider": "tavily"})
    for r in serper_search(query, max_results=max_results):
        results.append({"title": r.get("title", ""), "url": r.get("url", ""),
                        "content": (r.get("content") or "")[:600], "provider": "serper"})
    # 按 URL 去重
    seen, dedup = set(), []
    for r in results:
        key = r["url"] or r["title"]
        if key and key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup
