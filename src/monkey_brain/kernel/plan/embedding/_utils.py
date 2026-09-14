"""Shared math helpers for the embedding package.

All encoder output dimensions are EMBEDDING_DIM (32).
These are pure functions — no model state, no side effects.
"""

from __future__ import annotations

import json as _json
import logging
import re as _re
from collections import Counter as _Counter

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM: int = 32  # shared embedding space — every encoder produces R^32

_PROJ_RNG = np.random.default_rng(99)
_SHARED_PROJ: np.ndarray = _PROJ_RNG.normal(0, 1.0, (EMBEDDING_DIM, 256)).astype(np.float32)


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-8 else v


def _bow_project(text: str) -> np.ndarray:
    """TF-IDF bag-of-words with character bigrams → R^EMBEDDING_DIM.

    Produces semantically different embeddings for 'temperature CRITICAL 98°C'
    vs 'temperature nominal 37°C' — critical vocabulary shifts the TF weights.
    """
    text = text.lower()
    tokens = _re.findall(r"[a-z0-9_]+", text)
    bigrams = [text[i : i + 2] for i in range(len(text) - 1) if text[i : i + 2].strip()]
    all_tokens = tokens + bigrams
    if not all_tokens:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    tf = _Counter(all_tokens)
    total = sum(tf.values())
    raw = np.zeros(256, dtype=np.float32)
    for tok, cnt in tf.items():
        slot = hash(tok) % 256
        tf_val = cnt / total
        idf_approx = float(np.log(1.0 + 1.0 / tf_val))
        raw[slot] += tf_val * idf_approx
    raw = _l2(raw)
    feat = _SHARED_PROJ @ raw
    return np.tanh(_l2(feat))


def _parse_numbers(text: str) -> np.ndarray:
    """Extract all numeric values from text as a float array."""
    nums = []
    for m in _re.finditer(r"-?\d+\.?\d*(?:[eE][+-]?\d+)?", text):
        try:
            v = float(m.group())
            if abs(v) < 1e12:
                nums.append(v)
        except ValueError:
            logger.debug("_parse_numbers: suppressed exception", exc_info=True)
    return np.array(nums, dtype=np.float32) if nums else np.array([], dtype=np.float32)


def _ts_features(arr: np.ndarray) -> np.ndarray:
    """Time-series statistical feature vector → R^EMBEDDING_DIM.

    Features [0-14]: mean, std, min, max, range, median, skewness, kurtosis,
    trend slope, lag-1 autocorr, spectral entropy, zero-crossing rate, RMS,
    crest factor, coefficient of variation. [15-31]: zeros (reserved).
    """
    feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    n = len(arr)
    if n == 0:
        return feat
    mu = float(np.mean(arr))
    sig = float(np.std(arr)) + 1e-8
    mn = float(np.min(arr))
    mx = float(np.max(arr))
    scale = max(abs(mu), abs(mn), abs(mx), 1.0)
    feat[0] = mu / scale
    feat[1] = sig / scale
    feat[2] = mn / scale
    feat[3] = mx / scale
    feat[4] = (mx - mn) / scale
    feat[5] = float(np.median(arr)) / scale
    if n >= 3:
        feat[6] = float(np.clip(np.mean(((arr - mu) / sig) ** 3), -10, 10))
        feat[7] = float(np.clip(np.mean(((arr - mu) / sig) ** 4) - 3.0, -10, 10))
    if n >= 4:
        t = np.arange(n, dtype=np.float32)
        A = np.column_stack([t, np.ones(n)])
        slope = np.linalg.lstsq(A, arr, rcond=None)[0][0]
        feat[8] = float(np.clip(slope / scale, -5, 5))
    if n >= 5:
        x = arr - mu
        denom = float(np.dot(x, x)) + 1e-8
        feat[9] = float(np.clip(np.dot(x[:-1], x[1:]) / denom, -1, 1))
    if n >= 8:
        mag = np.abs(np.fft.rfft(arr - mu))
        prob = mag / (mag.sum() + 1e-8)
        nz = prob[prob > 1e-10]
        feat[10] = float(np.clip(-np.sum(nz * np.log(nz)) / np.log(max(len(prob), 2)), 0, 1))
    if n >= 2:
        signs = np.sign(arr[:-1]) != np.sign(arr[1:])
        feat[11] = float(np.sum(signs)) / (n - 1)
    rms = float(np.sqrt(np.mean(arr**2))) + 1e-8
    feat[12] = rms / scale
    feat[13] = float(np.clip(mx / rms, 0, 10)) / 10.0
    feat[14] = sig / (abs(mu) + 1e-8)
    return np.tanh(feat)


def _graph_features(content: str) -> np.ndarray:
    """Graph structural feature vector → R^EMBEDDING_DIM.

    Parses JSON adjacency dict, JSON edge list [[u,v],...], or text arrow
    notation (u → v / u -> v). Extracts node count, edge count, density,
    degree stats, clustering coeff, average shortest path (sampled BFS).
    """
    feat = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    graph: dict[str, list[str]] = {}
    try:
        data = _json.loads(content)
        if isinstance(data, dict):
            for k, v in data.items():
                graph[str(k)] = [str(x) for x in (v if isinstance(v, list) else [])]
        elif isinstance(data, list):
            for edge in data:
                if isinstance(edge, (list, tuple)) and len(edge) >= 2:
                    u, v = str(edge[0]), str(edge[1])
                    graph.setdefault(u, []).append(v)
                    graph.setdefault(v, []).append(u)
    except (ValueError, TypeError) as e:
        logger.debug("Graph JSON parse failed: %s", e)
    for u, v in _re.findall(r"(\w[\w. ]*?)\s*(?:→|->|–>)\s*([\w. ]+)", content):
        u, v = u.strip(), v.strip()
        if u and v:
            graph.setdefault(u, []).append(v)
    if not graph:
        feat[0] = float(len(_re.findall(r"\bnode\b|\bvertex\b|\bentity\b", content, _re.I))) / 100.0
        feat[1] = float(len(_re.findall(r"\bedge\b|\blink\b|\brelation\b", content, _re.I))) / 100.0
        return np.tanh(feat)
    nodes = list(graph.keys())
    n = len(nodes)
    degrees = [len(graph[nd]) for nd in nodes]
    total_edges = sum(degrees)
    max_edges = max(n * (n - 1), 1)
    feat[0] = min(n, 1000) / 1000.0
    feat[1] = min(total_edges, 10000) / 10000.0
    feat[2] = total_edges / max_edges
    feat[3] = float(np.mean(degrees)) / max(n, 1)
    feat[4] = float(max(degrees)) / max(n, 1)
    feat[5] = float(np.std(degrees)) / max(n, 1)
    mean_d = float(np.mean(degrees)) + 1e-8
    feat[6] = float(np.std(degrees)) / mean_d
    try:
        tri = 0
        for nd in nodes[:20]:
            nbrs = set(graph[nd])
            for nb in list(nbrs)[:10]:
                tri += len(nbrs & set(graph.get(nb, [])))
        possible = max(total_edges * (total_edges - 1), 1)
        feat[7] = min(3 * tri, possible) / possible
    except (KeyError, TypeError, IndexError) as e:
        logger.debug("Clustering coefficient failed: %s", e)
    try:
        from collections import deque

        total_hops, sample_count = 0, 0
        for src in nodes[:5]:
            visited = {src: 0}
            q = deque([src])
            while q:
                nd = q.popleft()
                for nb in graph.get(nd, []):
                    if nb not in visited:
                        visited[nb] = visited[nd] + 1
                        q.append(nb)
            if len(visited) > 1:
                total_hops += sum(visited.values()) / (len(visited) - 1)
                sample_count += 1
        if sample_count:
            feat[8] = min(total_hops / sample_count, 20) / 20.0
    except (KeyError, TypeError, IndexError, ZeroDivisionError) as e:
        logger.debug("BFS shortest path failed: %s", e)
    return np.tanh(feat)
