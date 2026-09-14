"""SparseTransitionTensor — the flattened world model  W[d, i, j, f]  (ADR 0021).

One sparse higher-order tensor instead of many specialized graphs:

    d  = domain            (manufacturing, weather, finance, robotics, beekeeping, …)
    i  = source state      (an agent / capability, GLOBALLY indexed)
    j  = destination state
    f  = feature           (probability, Q-value, reward, latency, cost, confidence,
                            uncertainty, frequency, recency)

Every graph is a slice/projection  W[d,:,:] — the execution graph is the subgraph
currently traversed, the world model is the whole tensor, Bellman learning updates the
values of the traversed transitions. Domains that only coordinate internally are
block-diagonal; cross-domain transitions (weather → crop yield → supply → price →
revenue) are sparse OFF-diagonal entries, so planning is propagation through one giant
sparse tensor rather than jumping between graphs.

Nothing is hardcoded: domains and states are interned lazily as real transitions are
observed from execution traces. `to_operator()` projects any slice into a CompiledOperator
(CSR) for propagation — reusing the compiler, keeping learning and execution one primitive:
sparse tensor update + sparse tensor traversal.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from enum import IntEnum
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.compiler import GraphCompiler
from src.monkey_brain.kernel.compile.types import (
    CompiledOperator,
    SemanticGraphSnapshot,
)

logger = logging.getLogger("agentos.compile.tensor")


def _synchronized(method):
    """Run `method` while holding the instance's reentrant `_lock`.

    Used to make index-structure reads/writes mutually exclusive with the structural
    rewrites (compact/evict) that TensorCompactor's daemon can trigger on another thread.
    """
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Feature(IntEnum):
    """The f axis. Derived features (PROBABILITY, means) are computed on read;
    learned/observed accumulators are stored."""

    PROBABILITY = 0  # derived: freq(i,j) / Σ_k freq(i,k)
    Q_VALUE = 1  # learned via Bellman
    REWARD = 2  # mean observed reward
    LATENCY = 3  # mean latency (ms)
    COST = 4  # mean cost
    CONFIDENCE = 5  # mean confidence
    UNCERTAINTY = 6  # 1 / (1 + freq)  — shrinks as evidence accumulates
    FREQUENCY = 7  # observation count
    RECENCY = 8  # last-updated timestamp


NUM_FEATURES = len(Feature)


class _Cell:
    """Sparse entry W[·, i, j, :] — accumulators; derived features computed on read."""

    __slots__ = (
        "freq",
        "q",
        "reward_sum",
        "latency_sum",
        "cost_sum",
        "conf_sum",
        "last_ts",
    )

    def __init__(self) -> None:
        self.freq = 0
        self.q = 0.5  # neutral prior (matches QTable)
        self.reward_sum = 0.0
        self.latency_sum = 0.0
        self.cost_sum = 0.0
        self.conf_sum = 0.0
        self.last_ts = 0.0


class SparseTransitionTensor:
    """W[d, i, j, f] — a sparse, feature-valued, multi-domain transition tensor."""

    def __init__(
        self,
        learning_rate: float = 0.1,
        discount: float = 0.95,
        max_cells: int = 1_000_000,
    ) -> None:
        self._lr = learning_rate
        self._discount = discount
        self._max_cells = max_cells
        self._state_index: dict[str, int] = {}
        self._state_name: list[str] = []
        self._state_domain: list[str] = []
        self._domains: set[str] = set()
        self._cells: dict[tuple[int, int], _Cell] = {}  # (i,j) -> accumulators
        self._out: dict[int, dict[int, _Cell]] = {}  # i -> {j: cell}  outgoing adjacency
        self._in: dict[int, dict[int, _Cell]] = {}  # j -> {i: cell}  incoming adjacency
        self._ref: dict[int, int] = {}  # state idx -> #cells touching it
        self._free: list[int] = []  # freed index slots (reuse pool)
        self._out_freq: dict[int, int] = {}  # Σ_k freq(i,k) for probability
        self._revision: int = 0  # bumps on each batch update
        self._dirty: set[tuple[int, int]] = set()  # recently modified cells (for incremental ops)
        self._eviction_count: int = 0  # total cells evicted
        self._compiler: GraphCompiler | None = None  # reused across to_operator() calls
        self._built: bool = False  # one-time build step completed
        # Reentrant so the structural rewrites (compact/evict) exclude concurrent
        # reads/writes. Without it, TensorCompactor's daemon thread could remap every
        # index (compact()) while an actor mid-feature() has already resolved a now-stale
        # (i, j) — a torn read against the new _cells. RLock because the guarded mutators
        # call each other (bellman_update → intern → _new_cell; compact → _maybe_free).
        # The actor cycle is single-threaded, so this lock is uncontended on the hot path;
        # it only ever blocks when the compactor is actually rewriting the index.
        self._lock = threading.RLock()

    @property
    def revision(self) -> int:
        return self._revision

    @_synchronized
    def _build_if_needed(self) -> None:
        """One-time build step. Idempotent — a tensor already built is a no-op.

        States/cells are interned lazily as transitions are observed, so there is no
        separate structural build phase today; this just marks the tensor ready so
        callers (e.g. the pipeline's world init) have a single, safe entry point.
        """
        if self._built:
            return
        self._built = True

    def is_built(self) -> bool:
        return self._built

    # ── interning (lazy, no hardcoding) ──────────────────────────────────────────

    @_synchronized
    def intern(self, state: str, domain: str = "default") -> int:
        idx = self._state_index.get(state)
        if idx is None:
            if self._free:  # reuse a freed slot
                idx = self._free.pop()
                self._state_name[idx] = state
                self._state_domain[idx] = domain
            else:
                idx = len(self._state_name)
                self._state_name.append(state)
                self._state_domain.append(domain)
            self._state_index[state] = idx
            self._domains.add(domain)
            self._ref[idx] = 0
        return idx

    def _new_cell(self, i: int, j: int) -> "_Cell":
        """Create the sparse cell (i,j), wire both adjacency indexes, account references."""
        cell = _Cell()
        self._cells[(i, j)] = cell
        self._out.setdefault(i, {})[j] = cell
        self._in.setdefault(j, {})[i] = cell
        self._ref[i] = self._ref.get(i, 0) + 1
        self._ref[j] = self._ref.get(j, 0) + 1
        return cell

    def _maybe_free(self, idx: int) -> None:
        """Reclaim a state slot once no cell references it."""
        if self._ref.get(idx, 0) > 0:
            return
        if idx >= len(self._state_name) or self._state_name[idx] is None:
            return
        self._state_index.pop(self._state_name[idx], None)
        self._state_name[idx] = None
        self._state_domain[idx] = None
        self._ref.pop(idx, None)
        self._out_freq.pop(idx, None)
        self._out.pop(idx, None)
        self._in.pop(idx, None)
        self._free.append(idx)

    def mark_dirty(self, src: str, dst: str) -> None:
        """Mark a cell as dirty for incremental propagation."""
        i = self._state_index.get(src)
        j = self._state_index.get(dst)
        if i is not None and j is not None:
            self._dirty.add((i, j))

    def clear_dirty(self) -> None:
        self._dirty.clear()

    def _drop_cell(self, i: int, j: int) -> bool:
        """Tear down cell (i,j) completely: cells, BOTH adjacency indexes, the
        out-frequency total, refcounts, dirty set.

        The one place a cell is destroyed. remove() and evict() each used to do this
        by hand and each got it wrong in a different way — remove() never cleaned the
        INCOMING index (leaving _in pointing at a deleted cell), and evict() never
        decremented _out_freq (leaving PROBABILITY divided by a stale total).
        """
        cell = self._cells.pop((i, j), None)
        if cell is None:
            return False

        row = self._out.get(i)
        if row is not None:
            row.pop(j, None)
            if not row:
                self._out.pop(i, None)
        col = self._in.get(j)
        if col is not None:
            col.pop(i, None)
            if not col:
                self._in.pop(j, None)

        self._out_freq[i] = max(0, self._out_freq.get(i, 0) - cell.freq)
        if not self._out_freq[i]:
            self._out_freq.pop(i, None)
        self._dirty.discard((i, j))

        # Mirror _new_cell's accounting exactly: it credits i and j separately, so a
        # self-loop holds TWO references and must give both back.
        self._ref[i] = self._ref.get(i, 0) - 1
        self._ref[j] = self._ref.get(j, 0) - 1
        self._maybe_free(i)
        self._maybe_free(j)
        return True

    @_synchronized
    def evict(self, target_ratio: float = 0.8) -> int:
        """Evict low-frequency cells when over capacity.

        Removes the least-frequently-observed cells until nnz is back to
        max_cells * target_ratio. Returns the number of cells evicted.
        """
        if len(self._cells) <= self._max_cells:
            return 0

        # Sort cells by frequency (ascending) — evict least-used first
        sorted_cells = sorted(self._cells.items(), key=lambda kv: kv[1].freq)
        evict_count = len(self._cells) - int(self._max_cells * target_ratio)
        evicted = 0

        # `if cell.freq > 0: continue` used to sit here, meaning "don't evict actively
        # used cells" — but EVERY observed cell has freq >= 1, so only cells created by
        # bellman_update and never observed could ever be evicted. On real traffic
        # eviction removed nothing and max_cells was not a bound at all: the tensor grew
        # without limit. Least-frequently-used IS the eviction policy; the sort already
        # protects the hot cells by putting them last.
        for (i, j), _cell in sorted_cells[:evict_count]:
            if self._drop_cell(i, j):
                evicted += 1

        self._eviction_count += evicted
        if evicted:
            logger.info(
                "[tensor] evicted %d cells (total=%d, nnz=%d)",
                evicted,
                self._eviction_count,
                len(self._cells),
            )
        return evicted

    @_synchronized
    def compact(self) -> int:
        """Remove holes from the state table. Returns the number of states compacted."""
        live = [i for i in range(len(self._state_name)) if self._state_name[i] is not None]
        if len(live) == len(self._state_name):
            return 0  # no holes

        remap = {old: new for new, old in enumerate(live)}
        old_count = len(self._state_name)

        # Remap state indices
        new_names = [self._state_name[i] for i in live]
        new_domains = [self._state_domain[i] for i in live]
        new_index = {name: idx for idx, name in enumerate(new_names)}

        # Remap cell indices
        new_cells = {}
        new_out = {}
        new_in = {}
        for (i, j), cell in self._cells.items():
            ni, nj = remap.get(i), remap.get(j)
            if ni is not None and nj is not None:
                new_cells[(ni, nj)] = cell
                new_out.setdefault(ni, {})[nj] = cell
                new_in.setdefault(nj, {})[ni] = cell

        self._state_name = new_names
        self._state_domain = new_domains
        self._state_index = new_index
        self._cells = new_cells
        self._out = new_out
        self._in = new_in
        self._free = []

        # _out_freq is keyed by state INDEX and every index just moved. It was left
        # untouched here, so after a compaction PROBABILITY — cell.freq / _out_freq[i],
        # the denominator D in the operator M = D⁻¹W — was dividing by whatever
        # unrelated state used to hold that index, or by nothing at all (→ 0.0). A
        # compaction silently zeroed the transition matrix the planner propagates over.
        self._out_freq = {remap[i]: total for i, total in self._out_freq.items() if i in remap}
        self._dirty = {(remap[i], remap[j]) for (i, j) in self._dirty if i in remap and j in remap}

        # Count i and j separately, exactly as _new_cell credits them — a self-loop
        # holds two references. Counting each cell once undercounted every self-loop
        # state (which is every agent: QTable stores agent quality as self-loop cells),
        # so a later remove()/evict() could drive the refcount to zero and free a state
        # that still had live cells pointing at it.
        new_ref: dict[int, int] = {}
        for a, b in new_cells:
            new_ref[a] = new_ref.get(a, 0) + 1
            new_ref[b] = new_ref.get(b, 0) + 1
        self._ref = new_ref

        compacted = old_count - len(new_names)
        if compacted:
            logger.info(
                "[tensor] compacted %d states (old=%d, new=%d)",
                compacted,
                old_count,
                len(new_names),
            )
        return compacted

    @property
    def capacity_usage(self) -> float:
        """Fraction of max_cells currently in use."""
        return len(self._cells) / max(self._max_cells, 1)

    # ── observe (sparse update from an execution trace) ──────────────────────────

    @_synchronized
    def observe(
        self,
        src: str,
        dst: str,
        *,
        domain: str = "default",
        dst_domain: str | None = None,
        reward: float = 0.0,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        confidence: float = 0.0,
        ts: float | None = None,
        weight: float = 1.0,
    ) -> None:
        """Record one observed transition src→dst with optional trust weighting.

        Interns states lazily and updates only the single sparse cell W[·, i, j, :]
        — the tiny subset a real execution touches.

        Args:
            weight: trust-weighted frequency increment (0-1). Default 1.0 (no penalty).
                    This implements W' = f(W, O, T) by weighting observation influence.
                    High-trust sources (weight=0.9+) heavily influence world state.
                    Low-trust sources (weight=0.3) have minimal influence.
        """
        i = self.intern(src, domain)
        j = self.intern(dst, dst_domain or domain)
        cell = self._cells.get((i, j))
        if cell is None:
            cell = self._new_cell(i, j)
        cell.freq += weight  # Trust-weighted frequency: high trust → higher freq
        cell.reward_sum += reward
        cell.latency_sum += latency_ms
        cell.cost_sum += cost
        cell.conf_sum += confidence
        cell.last_ts = ts if ts is not None else time.time()
        self._out_freq[i] = self._out_freq.get(i, 0) + weight  # Weighted out-degree

    @_synchronized
    def bellman_update(
        self,
        src: str,
        dst: str,
        reward: float,
        *,
        next_best_q: float | None = None,
        domain: str = "default",
    ) -> float:
        """Sparse Bellman update of W[·, i, j, Q]. Touches one cell — the traversed
        transition. Q ← Q + α(r + γ·maxₖ Q(j→k) − Q)."""
        i = self.intern(src, domain)
        j = self.intern(dst, domain)
        cell = self._cells.get((i, j))
        if cell is None:
            cell = self._new_cell(i, j)
            self._out_freq[i] = self._out_freq.get(i, 0)
        nq = next_best_q if next_best_q is not None else self._max_out_q(j)
        cell.q += self._lr * (reward + self._discount * nq - cell.q)
        return cell.q

    def _max_out_q(self, i: int) -> float:
        """max_k Q(i→k) via the adjacency index — O(out-degree), not O(nnz).

        A state with no outgoing transition is terminal in this model (nothing was
        ever observed to follow it), so its future value is 0. This used to return
        0.5, which is an optimistic INITIALISATION value — legitimate for an
        unvisited (s,a) pair, but wrong as a bootstrap target: it added a phantom
        γ·0.5 = 0.475 of future value to every terminal transition's Bellman target,
        biasing Q upward everywhere the graph ends.
        """
        row = self._out.get(i)
        return max((c.q for c in row.values()), default=0.0) if row else 0.0

    # ── batch world update (structural — the world is not runtime-fixed) ─────────

    @_synchronized
    def batch_update(self, transitions: Iterable[Mapping], *, source: str = "") -> int:
        """Apply a batch of transitions atomically and bump the revision.

        This is how the world changes STRUCTURALLY — adding a store, a domain, new
        capabilities — distinct from per-action runtime changes (which are local) and
        from context changes (which are queued). Returns the new revision.
        """
        count = 0
        for t in transitions:
            self.observe(
                t["src"],
                t["dst"],
                domain=t.get("domain", "default"),
                dst_domain=t.get("dst_domain"),
                reward=t.get("reward", 0.0),
                latency_ms=t.get("latency_ms", 0.0),
                cost=t.get("cost", 0.0),
                confidence=t.get("confidence", 0.0),
            )
            count += 1
        self._revision += 1
        logger.info(
            "[tensor] batch update: +%d transitions%s → revision %d (states=%d, nnz=%d)",
            count,
            f" from {source}" if source else "",
            self._revision,
            len(self._state_name),
            len(self._cells),
        )
        _obs.counter("world.batch_update")
        _obs.gauge("world.revision", float(self._revision))
        _obs.gauge("world.transitions", float(len(self._cells)))
        return self._revision

    def add_store(self, store: str, domain: str, transitions: Iterable[Mapping]) -> int:
        """Add a new store/provider as a batch — a new domain and its transitions join
        the world (changes it, new revision)."""
        logger.info("[tensor] adding store %r (domain=%r)", store, domain)
        norm = [{**t, "domain": t.get("domain", domain)} for t in transitions]
        return self.batch_update(norm, source=f"store:{store}")

    # ── persistence ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        # Compact away freed slots so persisted indices are dense (holes never leak to disk).
        live = [i for i in range(len(self._state_name)) if self._state_name[i] is not None]
        remap = {old: new for new, old in enumerate(live)}
        return {
            "lr": self._lr,
            "discount": self._discount,
            "revision": self._revision,
            "states": [{"name": self._state_name[i], "domain": self._state_domain[i]} for i in live],
            "cells": [
                {
                    "i": remap[i],
                    "j": remap[j],
                    "freq": c.freq,
                    "q": c.q,
                    "reward_sum": c.reward_sum,
                    "latency_sum": c.latency_sum,
                    "cost_sum": c.cost_sum,
                    "conf_sum": c.conf_sum,
                    "last_ts": c.last_ts,
                }
                for (i, j), c in self._cells.items()
            ],
        }

    def load_dict(self, d: Mapping) -> None:
        self.__init__(d.get("lr", 0.1), d.get("discount", 0.95))
        for s in d.get("states", []):
            self.intern(s["name"], s.get("domain", "default"))
        for c in d.get("cells", []):
            i, j = c["i"], c["j"]
            cell = _Cell()
            cell.freq = c["freq"]
            cell.q = c["q"]
            cell.reward_sum = c["reward_sum"]
            cell.latency_sum = c["latency_sum"]
            cell.cost_sum = c["cost_sum"]
            cell.conf_sum = c["conf_sum"]
            cell.last_ts = c["last_ts"]
            self._cells[(i, j)] = cell
            self._out.setdefault(i, {})[j] = cell
            # _in was never rebuilt on load, so a tensor restored from disk had an
            # empty incoming index while holding cells — the invariant _new_cell
            # establishes. Latent today (nothing queries _in yet), corrupt the moment
            # anything does.
            self._in.setdefault(j, {})[i] = cell
            self._ref[i] = self._ref.get(i, 0) + 1
            self._ref[j] = self._ref.get(j, 0) + 1
            self._out_freq[i] = self._out_freq.get(i, 0) + cell.freq
        self._revision = d.get("revision", 0)

    def save(self, path: str | Path) -> None:
        # Atomic: write a temp file then os.replace — a crash mid-write can never leave a
        # half-written world readable as truth.
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()
        logger.info(
            "[tensor] saved world (rev %d, %d transitions) → %s",
            self._revision,
            len(self._cells),
            p,
        )

    def load(self, path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            self.load_dict(json.loads(p.read_text()))
            logger.info(
                "[tensor] loaded world (rev %d, %d transitions) ← %s",
                self._revision,
                len(self._cells),
                p,
            )

    # ── the f axis: W[·, i, j, f] ────────────────────────────────────────────────

    @_synchronized
    def feature(self, src: str, dst: str, f: Feature) -> float:
        i = self._state_index.get(src)
        j = self._state_index.get(dst)
        if i is None or j is None:
            return 0.0
        cell = self._cells.get((i, j))
        if cell is None:
            return 0.0
        if f is Feature.PROBABILITY:
            out = self._out_freq.get(i, 0)
            return cell.freq / out if out else 0.0
        if f is Feature.Q_VALUE:
            return cell.q
        if f is Feature.FREQUENCY:
            return float(cell.freq)
        if f is Feature.UNCERTAINTY:
            return 1.0 / (1.0 + cell.freq)
        if f is Feature.RECENCY:
            return cell.last_ts
        denom = cell.freq or 1
        if f is Feature.REWARD:
            return cell.reward_sum / denom
        if f is Feature.LATENCY:
            return cell.latency_sum / denom
        if f is Feature.COST:
            return cell.cost_sum / denom
        if f is Feature.CONFIDENCE:
            return cell.conf_sum / denom
        return 0.0

    def feature_vector(self, src: str, dst: str) -> list[float]:
        """The full F-vector W[·, i, j, :] on one transition."""
        return [self.feature(src, dst, Feature(f)) for f in range(NUM_FEATURES)]

    # ── slices / projections ─────────────────────────────────────────────────────

    def domains(self) -> list[str]:
        """The domains the world CURRENTLY holds states in.

        Derived from live states, not from the `_domains` set. `_domains` only ever GREW:
        intern() added to it, but remove()/evict()/compact() never pruned it, so a store
        whose states were all withdrawn (or a domain that was requested on a re-intern
        but never actually applied — first-domain-wins) stayed listed forever. A "which
        domains does the world know about" query must reflect what is actually there.
        """
        return sorted({self._state_domain[i] for i in range(len(self._state_name)) if self._state_name[i] is not None})

    def states(self, domain: str | None = None) -> list[str]:
        if domain is None:
            return [n for n in self._state_name if n is not None]
        return [
            self._state_name[i]
            for i in range(len(self._state_name))
            if self._state_name[i] is not None and self._state_domain[i] == domain
        ]

    @_synchronized
    def slice(
        self,
        domain: str | None = None,
        f: Feature = Feature.PROBABILITY,
        intra_only: bool = True,
    ) -> dict[tuple[str, str], float]:
        """A transition-matrix view W[d,:,:,f]. `domain=None` returns the whole world
        (all states, incl. cross-domain off-diagonals). `intra_only` keeps a domain's
        block on the diagonal."""
        out: dict[tuple[str, str], float] = {}
        for i, j in self._cells:
            di, dj = self._state_domain[i], self._state_domain[j]
            if domain is not None:
                if di != domain:
                    continue
                if intra_only and dj != domain:
                    continue
            si, sj = self._state_name[i], self._state_name[j]
            out[(si, sj)] = self.feature(si, sj, f)
        return out

    @_synchronized
    def remove(self, src: str, dst: str) -> bool:
        """Delete a transition (used by revoke / knowledge withdrawal). Returns True if
        a cell was removed."""
        i = self._state_index.get(src)
        j = self._state_index.get(dst)
        if i is None or j is None:
            return False
        return self._drop_cell(i, j)

    @_synchronized
    def successors(self, state: str) -> list[tuple[str, str]]:
        """Out-transitions (dst_state, dst_domain) of a state — used to query the world
        (an actor 'asks a question' about a state and gets its true successors)."""
        i = self._state_index.get(state)
        if i is None:
            return []
        return [(self._state_name[b], self._state_domain[b]) for b in self._out.get(i, {})]

    def domain_of(self, state: str) -> str:
        return self._domain_of(state)

    def cross_domain_edges(self) -> list[tuple[str, str, str, str]]:
        """Off-diagonal entries: (src, src_domain, dst, dst_domain) where domains differ."""
        edges = []
        for i, j in self._cells:
            if self._state_domain[i] != self._state_domain[j]:
                edges.append(
                    (
                        self._state_name[i],
                        self._state_domain[i],
                        self._state_name[j],
                        self._state_domain[j],
                    )
                )
        return edges

    @_synchronized
    def to_operator(
        self,
        domain: str | None = None,
        f: Feature = Feature.PROBABILITY,
        compiler: GraphCompiler | None = None,
        intra_only: bool = True,
    ) -> CompiledOperator:
        """Project a slice into a CompiledOperator (CSR) for propagation. domain=None
        compiles the whole world model as one giant sparse operator (cross-domain edges
        included)."""
        # The tensor outlives any single call, so hold the compiler (and therefore its
        # LRU operator cache) instead of rebuilding it — and its cache — every time.
        # A caller-supplied compiler still wins.
        if compiler is None:
            if self._compiler is None:
                self._compiler = GraphCompiler()
            compiler = self._compiler
        cells = self.slice(domain, f=f, intra_only=intra_only)
        state_set = {s for pair in cells for s in pair}
        if domain is not None and intra_only:
            state_set |= set(self.states(domain))
        nodes = [{"id": s, "agent": s, "domain": self._domain_of(s)} for s in sorted(state_set)]
        edges = [{"from": a, "to": b} for (a, b) in cells]
        strengths = {(a, b): w for (a, b), w in cells.items()}
        gid = f"world:{domain}" if domain else "world:all"
        return compiler.compile(SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths=strengths, graph_id=gid))

    def _domain_of(self, state: str) -> str:
        i = self._state_index.get(state)
        return self._state_domain[i] if i is not None else "default"

    # ── stats ────────────────────────────────────────────────────────────────────

    def nnz(self) -> int:
        return len(self._cells)

    def summary(self) -> dict:
        live = len(self._state_index)
        return {
            "domains": len(self.domains()),  # live domains, not the never-pruned _domains set
            "states": live,
            "transitions": len(self._cells),
            "cross_domain": len(self.cross_domain_edges()),
            "features": NUM_FEATURES,
            "density": len(self._cells) / max(live**2, 1),
        }

    def __iter__(self) -> Iterator[tuple[str, str]]:
        for i, j in self._cells:
            yield self._state_name[i], self._state_name[j]

    @_synchronized
    def has_edge(self, src: str, dst: str) -> bool:
        """Is (src, dst) present? O(1).

        Callers asking "does this tensor hold this edge" were reaching for
        `(src, dst) in set(tensor)`, which materializes EVERY edge in the world to
        answer one membership question — O(nnz) per call, on the actor read path.
        """
        i = self._state_index.get(src)
        j = self._state_index.get(dst)
        if i is None or j is None:
            return False
        return (i, j) in self._cells

    def __contains__(self, edge: object) -> bool:
        """`(src, dst) in tensor` — O(1), so the idiom is no longer a trap."""
        if not isinstance(edge, tuple) or len(edge) != 2:
            return False
        return self.has_edge(str(edge[0]), str(edge[1]))
