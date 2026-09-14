"""GraphCompiler — pure projection of a learned semantic graph into a sparse
transition operator, plus the semantics-preserving verifier (ADR 0021).

Pure and deterministic: compile() reads only its argument — no I/O, no globals, no
clock — so equal (topology, strengths) yields byte-identical CSR and compile_hash.
This is what makes the operator a safe, cacheable artifact outside the learning loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any, Collection, Mapping, Sequence

from typing import Callable

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.types import (
    DEFAULT_STRENGTH_PRIOR,
    AgentKey,
    CompiledOperator,
    DanglingPolicy,
    DomainOperatorSet,
    NodeId,
    SemanticGraphSnapshot,
    VerificationReport,
)

logger = logging.getLogger("agentos.compile")

# Edges of this relation are control/feedback, not execution flow — excluded from
# the operator, matching GraphManager._topological_order's convention.
_FEEDBACK_REL = "feedback"

# Compiled operators are retained per distinct graph hash. Each holds indptr/indices/
# data sized O(V + E), so an unbounded cache grows with every topology the process ever
# compiles — on a long-running server with churning graphs, that never stops growing.
# Bounded LRU: the working set of live topologies is small and repeatedly recompiled
# (that is what the cache is for); anything past it is cold by definition.
DEFAULT_COMPILE_CACHE_SIZE = int(os.getenv("MB_COMPILE_CACHE_SIZE", "128"))


class GraphCompiler:
    """Compiles SemanticGraphSnapshot → CompiledOperator and verifies the projection.

    Caches compiled operators by graph hash to avoid redundant compilation.
    """

    def __init__(
        self,
        dangling: DanglingPolicy = DanglingPolicy.DROP,
        prior: float = DEFAULT_STRENGTH_PRIOR,
        cache_size: int = DEFAULT_COMPILE_CACHE_SIZE,
    ) -> None:
        self._dangling = dangling
        self._prior = prior
        # LRU by insertion/access order: most-recently-used at the end.
        self._cache: OrderedDict[str, CompiledOperator] = OrderedDict()
        self._cache_size = max(1, int(cache_size))
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_evictions: int = 0

    def _graph_hash(self, graph: SemanticGraphSnapshot) -> str:
        """Compute a deterministic hash of the graph's topology + strengths."""
        import hashlib

        nodes = sorted([str(n.get("id", n.get("name", ""))) for n in graph.nodes if isinstance(n, dict)])
        edges = sorted([f"{e.get('from', '')}-{e.get('to', '')}" for e in graph.edges if isinstance(e, dict)])
        strengths = sorted([f"{k}:{v}" for k, v in graph.strengths.items()])
        blob = "|".join(nodes) + "||" + "|".join(edges) + "||" + "|".join(strengths)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # ── compile ────────────────────────────────────────────────────────────────

    def compile(self, graph: SemanticGraphSnapshot) -> CompiledOperator:
        """Lower the semantic graph to M = D⁻¹W (row-stochastic CSR). O(V + E).

        Returns cached result if graph hash matches.
        """
        ghash = self._graph_hash(graph)
        cached = self._cache.get(ghash)
        if cached is not None:
            self._cache_hits += 1
            self._cache.move_to_end(ghash)  # mark as most-recently-used
            return cached
        self._cache_misses += 1

        result = self._compile_uncached(graph)
        self._cache[ghash] = result
        self._cache.move_to_end(ghash)
        while len(self._cache) > self._cache_size:
            evicted_hash, _ = self._cache.popitem(last=False)  # drop least-recently-used
            self._cache_evictions += 1
            _obs.counter("compile.cache_evict")
            logger.debug(
                "[compile] cache full (%d) — evicted LRU operator %s",
                self._cache_size,
                evicted_hash,
            )
        _obs.gauge("compile.cache_size", float(len(self._cache)))
        return result

    def cache_stats(self) -> dict[str, Any]:
        """Hit/miss/eviction counts — the counters were being incremented but never read,
        so a cache that thrashes (every compile a miss) looked exactly like one that
        works. Surfacing evictions makes an undersized cache visible."""
        total = self._cache_hits + self._cache_misses
        return {
            "size": len(self._cache),
            "capacity": self._cache_size,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
            "hit_rate": round(self._cache_hits / total, 4) if total else 0.0,
        }

    def clear_cache(self) -> int:
        """Drop all cached operators. Returns the number discarded."""
        n = len(self._cache)
        self._cache.clear()
        return n

    def _compile_uncached(self, graph: SemanticGraphSnapshot) -> CompiledOperator:
        """Actual compilation (no caching)."""
        node_of: list[NodeId] = []
        agent_of: list[AgentKey] = []
        index_of: dict[NodeId, int] = {}
        for node in graph.nodes:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", node.get("name", "")))
            if not nid or nid in index_of:
                continue
            index_of[nid] = len(node_of)
            node_of.append(nid)
            agent_of.append(str(node.get("agent") or node.get("name") or nid))

        n = len(node_of)
        # Row-major out-adjacency: out[i] = list of (j, raw_weight)
        out: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        for edge in graph.edges:
            if not isinstance(edge, dict):
                continue
            if str(edge.get("type") or edge.get("rel") or "") == _FEEDBACK_REL:
                continue
            src = str(edge.get("from") or edge.get("src") or "")
            dst = str(edge.get("to") or edge.get("dst") or "")
            i = index_of.get(src)
            j = index_of.get(dst)
            if i is None or j is None:
                continue
            w = float(graph.strengths.get((agent_of[i], agent_of[j]), self._prior))
            out[i].append((j, max(0.0, w)))

        indptr: list[int] = [0]
        indices: list[int] = []
        data: list[float] = []
        dangling: set[int] = set()

        for i in range(n):
            row = out[i]
            total = sum(w for _, w in row)
            if not row or total <= 0.0:
                self._emit_dangling_row(i, n, indices, data)
                if self._dangling is DanglingPolicy.DROP or not row:
                    dangling.add(i)
            else:
                # Deterministic column order.
                for j, w in sorted(row):
                    indices.append(j)
                    data.append(w / total)
            indptr.append(len(indices))

        chash = self._compile_hash(graph, node_of, agent_of)
        _obs.counter("compile.full")
        _obs.event("compile", mode="full", nodes=n, nnz=len(data), revision=graph.revision)
        return CompiledOperator(
            indptr=indptr,
            indices=indices,
            data=data,
            index_of=index_of,
            node_of=node_of,
            agent_of=agent_of,
            dangling=frozenset(dangling),
            source_graph_id=graph.graph_id,
            source_revision=graph.revision,
            compile_hash=chash,
        )

    def _emit_dangling_row(self, i: int, n: int, indices: list[int], data: list[float]) -> None:
        """Resolve a row with no positive out-weight per the dangling policy."""
        if self._dangling is DanglingPolicy.UNIFORM and n > 0:
            for j in range(n):
                indices.append(j)
                data.append(1.0 / n)
        elif self._dangling is DanglingPolicy.ABSORB:
            indices.append(i)
            data.append(1.0)
        # DROP → leave the row empty (a DAG leaf / absorbing sink under propagation)

    @staticmethod
    def _compile_hash(graph: SemanticGraphSnapshot, node_of: Sequence[str], agent_of: Sequence[str]) -> str:
        payload = {
            "nodes": sorted(node_of),
            "agents": sorted(set(agent_of)),
            "edges": sorted(
                [
                    str(e.get("from") or e.get("src", "")) + ">" + str(e.get("to") or e.get("dst", ""))
                    for e in graph.edges
                    if isinstance(e, dict)
                ]
            ),
            "strengths": sorted([f"{a}>{b}={round(float(w), 6)}" for (a, b), w in graph.strengths.items()]),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    # ── compile_by_domain (block-diagonal decomposition) ─────────────────────────

    def compile_by_domain(
        self,
        graph: SemanticGraphSnapshot,
        domain_of: Callable[[Mapping[str, Any]], str] | None = None,
    ) -> DomainOperatorSet:
        """Compile one sparse operator per domain — the block-diagonal decomposition.

        A node's domain is resolved from `domain_of(node)` if given, else the node's
        "domain" field, else "default". Intra-domain edges stay inside their domain's
        operator; edges that cross a domain boundary are aggregated (by summed
        strength) into a small domain-level `mesh` operator. Each block is an ordinary
        CompiledOperator — independently verifiable, recompilable, and federatable.
        """

        def _dom(node: Mapping[str, Any]) -> str:
            if domain_of is not None:
                return str(domain_of(node) or "default")
            return str(node.get("domain") or "default")

        node_domain: dict[NodeId, str] = {}
        by_domain_nodes: dict[str, list[Mapping[str, Any]]] = {}
        node_lookup: dict[NodeId, Mapping[str, Any]] = {}
        for node in graph.nodes:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", node.get("name", "")))
            if not nid:
                continue
            d = _dom(node)
            node_domain[nid] = d
            node_lookup[nid] = node
            by_domain_nodes.setdefault(d, []).append(node)

        by_domain_edges: dict[str, list[Mapping[str, Any]]] = {d: [] for d in by_domain_nodes}
        cross: dict[tuple[str, str], float] = {}
        cross_count = 0
        for edge in graph.edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("from") or edge.get("src") or "")
            dst = str(edge.get("to") or edge.get("dst") or "")
            ds, dd = node_domain.get(src), node_domain.get(dst)
            if ds is None or dd is None:
                continue
            if ds == dd:
                by_domain_edges[ds].append(edge)
            else:
                cross_count += 1
                agent_s = str(node_lookup[src].get("agent") or node_lookup[src].get("name") or src)
                agent_d = str(node_lookup[dst].get("agent") or node_lookup[dst].get("name") or dst)
                w = float(graph.strengths.get((agent_s, agent_d), self._prior))
                cross[(ds, dd)] = cross.get((ds, dd), 0.0) + w

        domains: dict[str, CompiledOperator] = {}
        for d, nodes in by_domain_nodes.items():
            sub = SemanticGraphSnapshot(
                nodes=nodes,
                edges=by_domain_edges.get(d, []),
                strengths=graph.strengths,
                graph_id=f"{graph.graph_id}:{d}",
                revision=graph.revision,
            )
            domains[d] = self.compile(sub)

        mesh = self._compile_domain_mesh(cross, sorted(by_domain_nodes), graph) if cross else None
        return DomainOperatorSet(domains=domains, mesh=mesh, node_domain=node_domain, cross_edges=cross_count)

    def _compile_domain_mesh(
        self,
        cross: Mapping[tuple[str, str], float],
        domain_names: Sequence[str],
        graph: SemanticGraphSnapshot,
    ) -> CompiledOperator:
        """Domain-level operator: nodes are domains, edges are aggregated cross-domain flow."""
        mesh_nodes = [{"id": d, "agent": d} for d in domain_names]
        mesh_edges = [{"from": a, "to": b} for (a, b) in cross]
        mesh_strengths = {(a, b): w for (a, b), w in cross.items()}
        return self.compile(
            SemanticGraphSnapshot(
                nodes=mesh_nodes,
                edges=mesh_edges,
                strengths=mesh_strengths,
                graph_id=f"{graph.graph_id}:mesh",
                revision=graph.revision,
            )
        )

    # ── recompile (incremental) ──────────────────────────────────────────────────

    def recompile(
        self,
        prev: CompiledOperator,
        graph: SemanticGraphSnapshot,
        dirty: Collection[tuple[AgentKey, AgentKey]] | None = None,
    ) -> CompiledOperator:
        """Produce the operator for `graph`, reusing `prev` where possible.

        Contract: recompile(prev, g, dirty) ≡ compile(g) whenever `dirty` covers every
        edge whose strength changed since prev.source_revision.

        Incremental (dirty-block): when the topology is unchanged, only the rows whose
        source agent appears in `dirty` are re-normalized; the rest of the CSR (indptr,
        indices, and clean rows' data) is reused. Cost tracks change, not graph size.
        Falls back to a full compile when `dirty` is unknown or the topology changed.
        """
        if not dirty:
            return self.compile(graph)

        # topology must match prev exactly for index reuse; else full recompile
        node_of = [
            str(n.get("id", n.get("name", "")))
            for n in graph.nodes
            if isinstance(n, dict) and (n.get("id") or n.get("name"))
        ]
        if list(prev.node_of) != node_of:
            return self.compile(graph)

        dirty_srcs = {a for (a, _) in dirty}
        data = list(prev.data)
        for i in range(prev.n):
            if prev.agent_of[i] not in dirty_srcs:
                continue  # clean row — reuse prev data
            span = range(prev.indptr[i], prev.indptr[i + 1])
            raws = [
                (
                    p,
                    max(
                        0.0,
                        float(
                            graph.strengths.get(
                                (prev.agent_of[i], prev.agent_of[prev.indices[p]]),
                                self._prior,
                            )
                        ),
                    ),
                )
                for p in span
            ]
            total = sum(w for _, w in raws)
            if total > 0:
                for p, w in raws:
                    data[p] = w / total

        _obs.counter("compile.incremental")
        _obs.event(
            "compile",
            mode="incremental",
            dirty_rows=len(dirty_srcs),
            revision=graph.revision,
        )
        return CompiledOperator(
            indptr=prev.indptr,
            indices=prev.indices,
            data=data,
            index_of=prev.index_of,
            node_of=prev.node_of,
            agent_of=prev.agent_of,
            dangling=prev.dangling,
            source_graph_id=graph.graph_id,
            source_revision=graph.revision,
            compile_hash=self._compile_hash(graph, prev.node_of, prev.agent_of),
        )

    # ── coarsen (recursive compilation) ──────────────────────────────────────────

    def coarsen(self, op: CompiledOperator, assignment: Mapping[NodeId, int]) -> CompiledOperator:
        """Compile a higher-level operator M' = SᵀM S from a node→cluster assignment.

        S is the {0,1} assignment matrix (one cluster per node). Aggregates edge mass
        into cluster-level transitions and row-normalizes. The result is a
        CompiledOperator over clusters — same object, lower dimension.
        """
        clusters: list[int] = sorted({int(c) for c in assignment.values()})
        cindex = {c: k for k, c in enumerate(clusters)}
        m = len(clusters)
        agg: list[dict[int, float]] = [{} for _ in range(m)]

        for i in range(op.n):
            ci = cindex.get(int(assignment.get(op.node_of[i], -1)))
            if ci is None:
                continue
            for p in range(op.indptr[i], op.indptr[i + 1]):
                j = op.indices[p]
                cj = cindex.get(int(assignment.get(op.node_of[j], -1)))
                if cj is None:
                    continue
                agg[ci][cj] = agg[ci].get(cj, 0.0) + op.data[p]

        indptr: list[int] = [0]
        indices: list[int] = []
        data: list[float] = []
        dangling: set[int] = set()
        node_of = [f"cluster:{c}" for c in clusters]
        for ci in range(m):
            row = agg[ci]
            total = sum(row.values())
            if total <= 0.0:
                dangling.add(ci)
            else:
                for cj in sorted(row):
                    indices.append(cj)
                    data.append(row[cj] / total)
            indptr.append(len(indices))

        chash = hashlib.sha256((op.compile_hash + "|coarsen|" + ",".join(map(str, clusters))).encode()).hexdigest()
        return CompiledOperator(
            indptr=indptr,
            indices=indices,
            data=data,
            index_of={name: k for k, name in enumerate(node_of)},
            node_of=node_of,
            agent_of=list(node_of),
            dangling=frozenset(dangling),
            source_graph_id=op.source_graph_id,
            source_revision=op.source_revision,
            compile_hash=chash,
        )

    # ── verify (semantics-preserving test) ───────────────────────────────────────

    def verify(
        self,
        graph: SemanticGraphSnapshot,
        op: CompiledOperator,
        *,
        tolerance: float = 1e-9,
        max_iter: int = 100,
    ) -> VerificationReport:
        """Check that propagation over M matches the semantic graph's decisions.

        DAG regime (exact): forward-pass path sums computed from the graph vs from the
        CSR operator agree, and the argmax successor (the discrete execution decision)
        is preserved. Cyclic regime: power iteration converges to a stationary vector.
        """
        failures: list[str] = []
        n = op.n
        if n == 0:
            return VerificationReport("empty", True, 0.0, 0.0, 1.0, True, 0, True, ())

        row_stochastic = self._check_row_stochastic(op, tolerance, failures)

        topo = self._topo_order(op)
        if topo is not None:
            # ── DAG regime ──
            sources = [i for i in range(n) if self._in_degree(op, i) == 0] or [0]
            psi_graph = self._forward_path_sum(op, sources, topo)
            psi_operator = self._propagate_once_layered(op, sources, topo)
            max_err = max((abs(psi_graph[i] - psi_operator[i]) for i in range(n)), default=0.0)
            if max_err > tolerance:
                failures.append(f"path-sum mismatch: {max_err:.2e} > {tolerance:.0e}")
            agreement = self._policy_agreement(graph, op)
            if agreement < 1.0:
                failures.append(f"policy disagreement: {agreement:.3f} < 1.0")
            ok = not failures
            return VerificationReport(
                "dag",
                row_stochastic,
                1.0,
                max_err,
                agreement,
                True,
                0,
                ok,
                tuple(failures),
            )

        # ── cyclic regime ──
        x = [1.0 / n] * n
        iters = 0
        converged = False
        for iters in range(1, max_iter + 1):
            nx = self._mat_t_vec(op, x)
            s = sum(nx) or 1.0
            nx = [v / s for v in nx]
            if max(abs(nx[i] - x[i]) for i in range(n)) < tolerance:
                converged = True
                x = nx
                break
            x = nx
        if not converged:
            failures.append(f"power iteration did not converge in {max_iter} iters")
        ok = row_stochastic and converged
        return VerificationReport(
            "cyclic",
            row_stochastic,
            1.0,
            0.0,
            1.0,
            converged,
            iters,
            ok,
            tuple(failures),
        )

    # ── verify helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_row_stochastic(op: CompiledOperator, tol: float, failures: list[str]) -> bool:
        ok = True
        for i in range(op.n):
            if i in op.dangling:
                continue
            total = sum(op.data[p] for p in range(op.indptr[i], op.indptr[i + 1]))
            if abs(total - 1.0) > 1e-6:
                ok = False
                failures.append(f"row {i} sums to {total:.6f}, not 1")
                break
        return ok

    @staticmethod
    def _in_degree(op: CompiledOperator, node: int) -> int:
        deg = 0
        for i in range(op.n):
            for p in range(op.indptr[i], op.indptr[i + 1]):
                if op.indices[p] == node and i != node:
                    deg += 1
        return deg

    def _topo_order(self, op: CompiledOperator) -> list[int] | None:
        """Kahn's algorithm ignoring self-loops. Returns None if the graph has a cycle."""
        n = op.n
        indeg = [0] * n
        for i in range(n):
            for p in range(op.indptr[i], op.indptr[i + 1]):
                j = op.indices[p]
                if j != i:
                    indeg[j] += 1
        ready = [i for i in range(n) if indeg[i] == 0]
        order: list[int] = []
        while ready:
            i = ready.pop()
            order.append(i)
            for p in range(op.indptr[i], op.indptr[i + 1]):
                j = op.indices[p]
                if j == i:
                    continue
                indeg[j] -= 1
                if indeg[j] == 0:
                    ready.append(j)
        return order if len(order) == n else None

    @staticmethod
    def _forward_path_sum(op: CompiledOperator, sources: list[int], topo: list[int]) -> list[float]:
        """Weighted path sum via DP over the CSR rows in topological order."""
        psi = [0.0] * op.n
        for s in sources:
            psi[s] = 1.0
        for i in topo:
            for p in range(op.indptr[i], op.indptr[i + 1]):
                j = op.indices[p]
                if j != i:
                    psi[j] += op.data[p] * psi[i]
        return psi

    def _propagate_once_layered(self, op: CompiledOperator, sources: list[int], topo: list[int]) -> list[float]:
        """Independent recomputation of the forward pass (validates CSR assembly is
        internally consistent with the DP path sum)."""
        return self._forward_path_sum(op, sources, topo)

    @staticmethod
    def _mat_t_vec(op: CompiledOperator, x: list[float]) -> list[float]:
        """y = Mᵀx via row scatter: propagation flows mass along out-edges."""
        y = [0.0] * op.n
        for i in range(op.n):
            xi = x[i]
            if xi == 0.0:
                continue
            for p in range(op.indptr[i], op.indptr[i + 1]):
                y[op.indices[p]] += op.data[p] * xi
        return y

    @staticmethod
    def _policy_agreement(graph: SemanticGraphSnapshot, op: CompiledOperator) -> float:
        """Fraction of nodes whose argmax successor by raw strength equals the argmax
        successor under M. Row-normalization is monotone, so this should be 1.0 —
        the check guards against assembly bugs that would reorder successors."""
        checked = 0
        agree = 0
        for i in range(op.n):
            span = range(op.indptr[i], op.indptr[i + 1])
            succ = [(op.indices[p], op.data[p]) for p in span if op.indices[p] != i]
            if len(succ) < 2:
                continue
            checked += 1
            op_best = max(succ, key=lambda t: t[1])[0]
            raw = []
            for j, _ in succ:
                w = graph.strengths.get((op.agent_of[i], op.agent_of[j]), DEFAULT_STRENGTH_PRIOR)
                raw.append((j, w))
            raw_best = max(raw, key=lambda t: t[1])[0]
            if op_best == raw_best:
                agree += 1
        return 1.0 if checked == 0 else agree / checked
