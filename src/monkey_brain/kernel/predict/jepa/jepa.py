"""Jepa."""

from __future__ import annotations

import hashlib

import numpy as np

from src.monkey_brain.kernel.predict.jepa.base import (
    ISolver,
    SolverClass,
    SolverResult,
    _MODALITY_REGISTRY,
)


class JEPAWorldModel(ISolver):
    """Epistemic JEPA world model.

    Implements facebookresearch/jepa architecture adapted for the epistemic state
    E = (W, B, K, C, A, G, M). This is NOT image JEPA — the encoder does not operate
    on pixels. It encodes heterogeneous epistemic data:

      W — symbolic/numeric world state (hash-projected dict → z_W ∈ R^32)
      B — belief state (confidence, uncertainty, knowledge quality → z_B ∈ R^16)
      K — knowledge pack (item provenance, freshness, consistency → z_K ∈ R^8)
      A — affordance set (hash-sum over available capability name embeddings → z_A ∈ R^8)
      G — goal state (objective hash, predicate count, goal progress → z_G ∈ R^8)
      M — mesh state (agent count, role distribution, capability coverage → z_M ∈ R^8)

    Unified epistemic latent:
      z_E = [z_W | z_B | z_K | z_A | z_G | z_M] ∈ R^80

    Epistemic JEPA predictor:
      g_E(z_E, a_embed) → z'_E ∈ R^80  (predicts full next epistemic state)

    Each component encoder uses the right inductive bias for its data type:
      - Symbolic/numeric dicts → fixed random hash projection
      - Sets (affordances) → sum of hashed name embeddings (cardinality-invariant)
      - Strings (goals, actions) → character-level + hash embedding
      - Scalars (confidence, progress) → direct passthrough

    Loss (L1, latent-space prediction, no negative samples):
      L = mean(|z'_E - sg(z_E_target)|) + REG_COEFF · relu(1 - std(z'_E))

    EMA update: W_target ← EMA_M · W_target + (1 - EMA_M) · W_enc

    Backward-compatible interface maintained for epa_transition / epa_loss callers.
    """

    FEATURE_DIM = 64  # world encoder input (after hash projection)
    LATENT_DIM = 32  # z_W ∈ R^32  (world state latent)
    ACTION_DIM = 16  # action embedding
    BELIEF_IN = 6  # [confidence, unc.epistemic, unc.aleatoric, k_count_norm, consistency_avg, B.loss()]
    BELIEF_DIM = 16  # z_B ∈ R^16  (belief latent)
    EMA_M = 0.996  # EMA momentum
    LR = 0.01  # predictor learning rate
    REG_COEFF = 0.04  # variance collapse regularization weight

    # ── Epistemic JEPA extension: K, A, G, M component encoders ─────────────
    # Together with z_W and z_B, these form the unified epistemic latent z_E ∈ R^80.
    KNOWLEDGE_IN = (
        6  # [item_count_norm, avg_confidence, avg_epistemic_unc, avg_provenance, avg_freshness, consistency_avg]
    )
    KNOWLEDGE_DIM = 8  # z_K ∈ R^8  — retrieved knowledge quality + provenance
    AFFORDANCE_DIM = 8  # z_A ∈ R^8  — hash-sum over available capability name embeddings
    GOAL_IN = 8  # [4 obj-hash bytes, predicate_count_norm, progress, priority_norm, n_criteria_norm]
    GOAL_DIM = 8  # z_G ∈ R^8  — goal state
    MESH_IN = 6  # [agent_count_norm, active_ratio, role_entropy, goal_progress, cap_coverage, 0.0]
    MESH_DIM = 8  # z_M ∈ R^8  — agent mesh topology
    EPISTEMIC_DIM = 80  # LATENT_DIM + BELIEF_DIM + KNOWLEDGE_DIM + AFFORDANCE_DIM + GOAL_DIM + MESH_DIM

    name = "jepa"
    solver_class = SolverClass.JEPA

    def __init__(self) -> None:
        rng = np.random.default_rng(42)

        # ── World state encoder (W) ───────────────────────────────────────────
        # Fixed random projection: raw(256,) → features(FEATURE_DIM,)
        self._feat_proj: np.ndarray = rng.normal(0, 1.0, (self.FEATURE_DIM, 256)).astype(np.float32)

        scale = 1.0 / np.sqrt(self.FEATURE_DIM)
        self._W_enc: np.ndarray = rng.normal(0, scale, (self.LATENT_DIM, self.FEATURE_DIM)).astype(np.float32)
        self._b_enc: np.ndarray = np.zeros(self.LATENT_DIM, dtype=np.float32)
        self._W_target: np.ndarray = self._W_enc.copy()
        self._b_target: np.ndarray = self._b_enc.copy()

        pred_in = self.LATENT_DIM + self.ACTION_DIM
        self._W_pred: np.ndarray = rng.normal(0, 1.0 / np.sqrt(pred_in), (self.LATENT_DIM, pred_in)).astype(np.float32)
        self._b_pred: np.ndarray = np.zeros(self.LATENT_DIM, dtype=np.float32)

        # ── Belief encoder (B = K, C, U) ──────────────────────────────────────
        b_scale = 1.0 / np.sqrt(self.BELIEF_IN)
        self._W_enc_B: np.ndarray = rng.normal(0, b_scale, (self.BELIEF_DIM, self.BELIEF_IN)).astype(np.float32)
        self._b_enc_B: np.ndarray = np.zeros(self.BELIEF_DIM, dtype=np.float32)
        self._W_target_B: np.ndarray = self._W_enc_B.copy()
        self._b_target_B: np.ndarray = self._b_enc_B.copy()

        # Belief predictor: (z_W, a_embed) → z'_B
        # Conditioned on world latent + action, NOT on z_B — avoids circular dependence.
        b_pred_in = self.LATENT_DIM + self.ACTION_DIM
        self._W_pred_B: np.ndarray = rng.normal(0, 1.0 / np.sqrt(b_pred_in), (self.BELIEF_DIM, b_pred_in)).astype(
            np.float32
        )
        self._b_pred_B: np.ndarray = np.zeros(self.BELIEF_DIM, dtype=np.float32)

        # ── Knowledge pack encoder (K) ─────────────────────────────────────────
        # Encodes retrieved knowledge quality — provenance, freshness, consistency.
        # Unlike B which encodes belief about knowledge, K encodes the evidence itself.
        k_scale = 1.0 / np.sqrt(self.KNOWLEDGE_IN)
        self._W_enc_K: np.ndarray = rng.normal(0, k_scale, (self.KNOWLEDGE_DIM, self.KNOWLEDGE_IN)).astype(np.float32)
        self._b_enc_K: np.ndarray = np.zeros(self.KNOWLEDGE_DIM, dtype=np.float32)
        self._W_target_K: np.ndarray = self._W_enc_K.copy()
        self._b_target_K: np.ndarray = self._b_enc_K.copy()

        # ── Affordance set encoder (A) ─────────────────────────────────────────
        # Fixed embedding matrix: each capability name hashes to a slot,
        # z_A = sum of embeddings / sqrt(|A|) — cardinality-invariant.
        self._afford_embed: np.ndarray = rng.normal(0, 1.0, (self.AFFORDANCE_DIM, 256)).astype(
            np.float32
        )  # columns are per-slot embeddings

        # ── Goal encoder (G) ──────────────────────────────────────────────────
        # Encodes goal objective (hash), predicate structure, and progress estimate.
        g_scale = 1.0 / np.sqrt(self.GOAL_IN)
        self._W_enc_G: np.ndarray = rng.normal(0, g_scale, (self.GOAL_DIM, self.GOAL_IN)).astype(np.float32)
        self._b_enc_G: np.ndarray = np.zeros(self.GOAL_DIM, dtype=np.float32)
        self._W_target_G: np.ndarray = self._W_enc_G.copy()
        self._b_target_G: np.ndarray = self._b_enc_G.copy()

        # ── Mesh encoder (M) ──────────────────────────────────────────────────
        # Encodes agent topology — count, role distribution, capability coverage.
        m_scale = 1.0 / np.sqrt(self.MESH_IN)
        self._W_enc_M: np.ndarray = rng.normal(0, m_scale, (self.MESH_DIM, self.MESH_IN)).astype(np.float32)
        self._b_enc_M: np.ndarray = np.zeros(self.MESH_DIM, dtype=np.float32)
        self._W_target_M: np.ndarray = self._W_enc_M.copy()
        self._b_target_M: np.ndarray = self._b_enc_M.copy()

        # ── Unified epistemic predictor: g_E(z_E, a) → z'_E ─────────────────
        # One predictor over the full epistemic latent.
        # Cross-component learning: retrieval action → predict z'_K and z'_B jointly.
        e_pred_in = self.EPISTEMIC_DIM + self.ACTION_DIM
        self._W_pred_E: np.ndarray = rng.normal(0, 1.0 / np.sqrt(e_pred_in), (self.EPISTEMIC_DIM, e_pred_in)).astype(
            np.float32
        )
        self._b_pred_E: np.ndarray = np.zeros(self.EPISTEMIC_DIM, dtype=np.float32)
        self._W_target_E: np.ndarray = self._W_pred_E.copy()

        self._step = 0
        self._loss_history: list[float] = []
        self._belief_step = 0
        self._belief_loss_history: list[float] = []
        self._epistemic_step = 0
        self._epistemic_loss_history: list[float] = []

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _det_hash(val: str) -> int:
        """Deterministic hash — Python's built-in hash() is randomized across processes."""
        return int(hashlib.md5(val.encode()).hexdigest()[:8], 16)

    def _state_to_raw(self, state: dict) -> np.ndarray:
        """World state dict → dense 256-d raw feature vector."""
        raw = np.zeros(256, dtype=np.float32)
        for k, v in state.items():
            slot = self._det_hash(str(k)) % 256
            if isinstance(v, bool):
                raw[slot] = 1.0 if v else 0.0
            elif isinstance(v, (int, float)):
                raw[slot] = float(np.clip(v, -1e6, 1e6)) / 1e3
            elif isinstance(v, str):
                raw[slot] = float(self._det_hash(v) % 10_000) / 10_000.0
            elif isinstance(v, (dict, list, tuple)):
                raw[(slot + 1) % 256] = float(len(v)) / 100.0
        return raw

    def _extract_features(self, state: dict) -> np.ndarray:
        """state_dict → normalised feature vector (FEATURE_DIM,)."""
        raw = self._state_to_raw(state)
        feat = self._feat_proj @ raw  # (FEATURE_DIM,)
        norm = float(np.linalg.norm(feat)) + 1e-8
        return feat / norm

    def _embed_action(self, action: str) -> np.ndarray:
        """Action string → fixed-size embedding (ACTION_DIM,)."""
        vec = np.zeros(self.ACTION_DIM, dtype=np.float32)
        for i, ch in enumerate(action[: self.ACTION_DIM]):
            vec[i] = float(ord(ch)) / 128.0
        # hash remainder into last slot
        if action:
            vec[-1] = float(self._det_hash(action) % 10_000) / 10_000.0
        return vec

    # ------------------------------------------------------------------
    # Belief feature extraction + encoder
    # ------------------------------------------------------------------

    def _belief_to_raw(self, belief) -> np.ndarray:
        """BeliefState → 6-d raw feature vector (plain Python access only)."""
        raw = np.zeros(self.BELIEF_IN, dtype=np.float32)
        try:
            raw[0] = float(getattr(belief, "confidence", 0.5))
            unc = getattr(belief, "uncertainty", None)
            if unc is not None:
                raw[1] = float(getattr(unc, "epistemic", 0.0))
                raw[2] = float(getattr(unc, "aleatoric", 0.0))
            k = getattr(belief, "knowledge", [])
            raw[3] = float(len(k)) / 100.0
            if k:
                raw[4] = sum(getattr(ki, "consistency", 0.5) for ki in k) / len(k)
            raw[5] = float(belief.loss()) if callable(getattr(belief, "loss", None)) else 0.0
        except Exception as e:
            logger.debug("Exception caught: %s", e)
        return raw

    def _encode_belief(self, belief, *, target: bool = False) -> np.ndarray:
        """f_B(B) → z_B  (or  f̄_B(B) → z_B_target when target=True)."""
        raw = self._belief_to_raw(belief)
        if target:
            return np.tanh(self._W_target_B @ raw + self._b_target_B)
        return np.tanh(self._W_enc_B @ raw + self._b_enc_B)

    def _predict_belief(self, z_S: np.ndarray, a_embed: np.ndarray) -> np.ndarray:
        """g_B(z_S, a) → z'_B  (belief predictor conditioned on world latent + action)."""
        inp = np.concatenate([z_S, a_embed])
        return self._W_pred_B @ inp + self._b_pred_B

    # ------------------------------------------------------------------
    # Knowledge pack encoder (K)
    # ------------------------------------------------------------------

    def _knowledge_to_raw(self, knowledge_items) -> np.ndarray:
        """KnowledgeItem list → 6-d quality vector for z_K encoder.

        Full workload per item:
          Raw content (PDF, image, telemetry, graph, …)
              ↓  _MODALITY_REGISTRY.encode(item)  [modality-specific encoder]
          Embedding ∈ R^MODALITY_EMBED_DIM
              ↓  _MODALITY_REGISTRY.fuse(embeddings, weights=provenance_scores)
          Fused knowledge embedding  [evidence fusion]
              ↓  this method — extract 6 scalar features from the fused result
          raw ∈ R^6  →  z_K ∈ R^8  [epistemic encoder]

        JEPA never sees raw content. It sees the post-fusion epistemic representation.
        """
        raw = np.zeros(self.KNOWLEDGE_IN, dtype=np.float32)
        if not knowledge_items:
            return raw
        try:
            items = list(knowledge_items)
            n = len(items)
            raw[0] = float(min(n, 100)) / 100.0

            # Encode each item via its modality encoder
            embeddings: list[np.ndarray] = []
            weights: list[float] = []
            confs, unc_vals, prov_vals, fresh_vals, cons_vals = [], [], [], [], []
            for ki in items:
                try:
                    emb = _MODALITY_REGISTRY.encode(ki)
                    prov = float(getattr(ki, "provenance", 0.5))
                    embeddings.append(emb)
                    weights.append(prov)
                except Exception as e:
                    logger.debug("Exception caught: %s", e)
                confs.append(float(getattr(ki, "provenance", 0.5)))
                unc_vals.append(float(getattr(ki, "uncertainty", 0.0)))
                prov_vals.append(float(getattr(ki, "provenance", 0.5)))
                fresh_vals.append(float(getattr(ki, "freshness", 0.5)))
                cons_vals.append(float(getattr(ki, "consistency", 0.5)))

            # Evidence fusion: provenance-weighted mean embedding
            if embeddings:
                fused = _MODALITY_REGISTRY.fuse(embeddings, weights)
                # Project fused embedding into scalar quality scores
                # (the full fused vector is available for richer encoders)
                raw[1] = float(np.mean(fused[:8]))  # first 8 dims as content signal
                raw[2] = float(np.std(fused[:8]))  # embedding variance (diversity)
            else:
                raw[1] = sum(confs) / n
                raw[2] = sum(unc_vals) / n

            raw[3] = sum(prov_vals) / n  # mean provenance
            raw[4] = sum(fresh_vals) / n  # mean freshness
            raw[5] = sum(cons_vals) / n  # mean consistency
        except Exception as e:
            logger.debug("Exception caught: %s", e)
        return raw

    def _encode_knowledge(self, knowledge_items, *, target: bool = False) -> np.ndarray:
        """f_K(K) → z_K ∈ R^8."""
        raw = self._knowledge_to_raw(knowledge_items)
        if target:
            return np.tanh(self._W_target_K @ raw + self._b_target_K)
        return np.tanh(self._W_enc_K @ raw + self._b_enc_K)

    # ------------------------------------------------------------------
    # Affordance set encoder (A)
    # ------------------------------------------------------------------

    def _encode_affordances(self, affordances) -> np.ndarray:
        """f_A(A) → z_A ∈ R^8.

        Cardinality-invariant: hash each capability name to a column of the
        fixed embedding matrix, sum, then scale by 1/sqrt(|A|).
        Different from action embedding — this encodes the *available action space*,
        not the chosen action.
        """
        z = np.zeros(self.AFFORDANCE_DIM, dtype=np.float32)
        items = list(affordances) if affordances else []
        if not items:
            return z
        for name in items:
            slot = self._det_hash(str(name)) % 256
            z += self._afford_embed[:, slot]
        return np.tanh(z / max(1.0, float(len(items)) ** 0.5))

    # ------------------------------------------------------------------
    # Goal encoder (G)
    # ------------------------------------------------------------------

    def _goal_to_raw(self, goal, world_state: dict | None = None) -> np.ndarray:
        """GoalState → 8-d raw vector."""
        raw = np.zeros(self.GOAL_IN, dtype=np.float32)
        try:
            obj = getattr(goal, "objective", "") or ""
            h = self._det_hash(obj)
            # Spread 4 bytes of objective hash into first 4 slots
            for i in range(4):
                raw[i] = float((h >> (i * 8)) & 0xFF) / 255.0
            preds = getattr(goal, "target_predicates", []) or []
            raw[4] = float(min(len(preds), 20)) / 20.0  # predicate count (normalised)
            # Goal progress: how many predicates are satisfied in current world state
            if world_state and preds:
                met = sum(1 for p in preds if world_state.get(p))
                raw[5] = float(met) / float(len(preds))
            criteria = getattr(goal, "success_criteria", {}) or {}
            raw[6] = float(min(len(criteria), 20)) / 20.0
            raw[7] = float(getattr(goal, "priority", 0.5))
        except Exception as e:
            logger.debug("Exception caught: %s", e)
        return raw

    def _encode_goal(self, goal, world_state: dict | None = None, *, target: bool = False) -> np.ndarray:
        """f_G(G) → z_G ∈ R^8."""
        raw = self._goal_to_raw(goal, world_state)
        if target:
            return np.tanh(self._W_target_G @ raw + self._b_target_G)
        return np.tanh(self._W_enc_G @ raw + self._b_enc_G)

    # ------------------------------------------------------------------
    # Mesh encoder (M)
    # ------------------------------------------------------------------

    def _mesh_to_raw(self, mesh: dict) -> np.ndarray:
        """Mesh summary dict → 6-d raw vector."""
        raw = np.zeros(self.MESH_IN, dtype=np.float32)
        try:
            total = int(mesh.get("total_agents", mesh.get("agent_count", 0)))
            active = int(mesh.get("active_agents", total))
            raw[0] = float(min(total, 100)) / 100.0
            raw[1] = float(active) / max(float(total), 1.0)
            types = mesh.get("agent_types", [])
            if types:
                n = len(types)
                # role entropy proxy: 1 - max_freq/n (higher = more diverse)
                from collections import Counter

                freq = Counter(types)
                max_f = max(freq.values())
                raw[2] = 1.0 - float(max_f) / float(n)
            raw[3] = float(mesh.get("goal_progress", 0.0))
            caps = mesh.get("capability_coverage", 0.0)
            raw[4] = float(caps) if isinstance(caps, (int, float)) else 0.0
        except Exception as e:
            logger.debug("Exception caught: %s", e)
        return raw

    def _encode_mesh(self, mesh: dict, *, target: bool = False) -> np.ndarray:
        """f_M(M) → z_M ∈ R^8."""
        raw = self._mesh_to_raw(mesh)
        if target:
            return np.tanh(self._W_target_M @ raw + self._b_target_M)
        return np.tanh(self._W_enc_M @ raw + self._b_enc_M)

    # ------------------------------------------------------------------
    # Unified epistemic encoder: z_E = [z_W | z_B | z_K | z_A | z_G | z_M]
    # ------------------------------------------------------------------

    def _encode_epistemic(
        self,
        S: dict,
        B,
        K=None,
        A=None,
        G=None,
        M: dict | None = None,
        *,
        target: bool = False,
    ) -> np.ndarray:
        """f_E(W, B, K, A, G, M) → z_E ∈ R^80.

        Concatenates all six component latents.  Each component encoder uses
        the inductive bias appropriate for its data type:
          W — hash projection (symbolic/numeric dict)
          B — dense linear (scalar features)
          K — dense linear (knowledge metadata after modality fusion)
          A — hash-sum (set of capability name strings)
          G — dense linear (goal hash + scalar features)
          M — dense linear (mesh topology scalars)
        """
        z_W = self._encode(S, target=target)
        z_B = self._encode_belief(B, target=target)
        z_K = self._encode_knowledge(getattr(B, "knowledge", K or []), target=target)
        z_A = self._encode_affordances(A or set())
        z_G = (
            self._encode_goal(G, world_state=S, target=target)
            if G is not None
            else np.zeros(self.GOAL_DIM, dtype=np.float32)
        )
        z_M = self._encode_mesh(M or {}, target=target)
        return np.concatenate([z_W, z_B, z_K, z_A, z_G, z_M])  # R^80

    def _predict_epistemic(self, z_E: np.ndarray, a_embed: np.ndarray) -> np.ndarray:
        """g_E(z_E, a) → z'_E ∈ R^80.

        One unified predictor over the full epistemic latent.
        Cross-component learning: e.g., a retrieval action should jointly predict
        z'_K (new knowledge content) and z'_B (updated belief) and z'_W (world changes).
        """
        inp = np.concatenate([z_E, a_embed])
        return self._W_pred_E @ inp + self._b_pred_E

    def _epistemic_update(
        self,
        S_t: dict,
        B_t,
        A_t,
        G_t,
        M_t: dict,
        action: str,
        S_next: dict,
        B_next,
        A_next,
        G_next,
        M_next: dict,
    ) -> float:
        """Online epistemic JEPA update — one gradient step on the unified predictor.

        Predicts z'_E from current (S_t, B_t, K_t, A_t, G_t, M_t) and action,
        compares against target encoder applied to actual (S_next, …).
        """
        z_E = self._encode_epistemic(S_t, B_t, A=A_t, G=G_t, M=M_t)
        a_embed = self._embed_action(action)
        z_pred_E = self._predict_epistemic(z_E, a_embed)

        z_target_E = self._encode_epistemic(S_next, B_next, A=A_next, G=G_next, M=M_next, target=True)

        diff = z_pred_E - z_target_E
        loss_jepa = float(np.mean(np.abs(diff)))
        pstd = float(np.std(z_pred_E)) + 1e-8
        loss_reg = max(0.0, 1.0 - pstd)
        loss = loss_jepa + self.REG_COEFF * loss_reg

        grad = np.sign(diff) / self.EPISTEMIC_DIM
        inp = np.concatenate([z_E, a_embed])
        self._W_pred_E -= self.LR * np.outer(grad, inp)
        self._b_pred_E -= self.LR * grad

        # EMA on all component target encoders
        m = self.EMA_M
        for W, Wt, b, bt in [
            (self._W_enc, self._W_target, self._b_enc, self._b_target),
            (self._W_enc_B, self._W_target_B, self._b_enc_B, self._b_target_B),
            (self._W_enc_K, self._W_target_K, self._b_enc_K, self._b_target_K),
            (self._W_enc_G, self._W_target_G, self._b_enc_G, self._b_target_G),
            (self._W_enc_M, self._W_target_M, self._b_enc_M, self._b_target_M),
        ]:
            Wt[:] = m * Wt + (1 - m) * W
            bt[:] = m * bt + (1 - m) * b

        self._epistemic_step += 1
        self._epistemic_loss_history.append(loss)
        if len(self._epistemic_loss_history) > 1000:
            self._epistemic_loss_history = self._epistemic_loss_history[-1000:]
        return loss

    # ------------------------------------------------------------------
    # Encoder / predictor
    # ------------------------------------------------------------------

    def _encode(self, state: dict, *, target: bool = False) -> np.ndarray:
        """f(state) → z  (or  f̄(state) → z_target when target=True)."""
        feat = self._extract_features(state)
        if target:
            return np.tanh(self._W_target @ feat + self._b_target)
        return np.tanh(self._W_enc @ feat + self._b_enc)

    def _predict(self, z: np.ndarray, a_embed: np.ndarray) -> np.ndarray:
        """g(z, a) → z'  (predictor, no activation — operates in latent space)."""
        inp = np.concatenate([z, a_embed])
        return self._W_pred @ inp + self._b_pred

    # ------------------------------------------------------------------
    # Online JEPA update (one gradient step on predictor)
    # ------------------------------------------------------------------

    def _jepa_update(self, current_state: dict, action: str, next_state: dict) -> float:
        """Single online update step following facebookresearch/jepa training loop.

        1. Context encoder produces z from current_state.
        2. Predictor produces z' from (z, action).
        3. Target encoder produces z_target from next_state (stop-gradient).
        4. L1 loss + variance regularisation.
        5. Gradient step on predictor weights.
        6. EMA update of target encoder.
        """
        feat_ctx = self._extract_features(current_state)
        z = np.tanh(self._W_enc @ feat_ctx + self._b_enc)  # context latent

        a_embed = self._embed_action(action)
        z_pred = self._predict(z, a_embed)  # predicted target latent

        # Target encoder output — stop-gradient (no gradient flows through here)
        z_target = self._encode(next_state, target=True)

        # L1 JEPA loss
        diff = z_pred - z_target
        loss_jepa = float(np.mean(np.abs(diff)))

        # Variance collapse regularisation: relu(1 - std(z'))
        pstd = float(np.std(z_pred)) + 1e-8
        loss_reg = max(0.0, 1.0 - pstd)
        loss = loss_jepa + self.REG_COEFF * loss_reg

        # Predictor gradient: ∂L_jepa/∂z_pred = sign(diff) / LATENT_DIM
        grad_pred = np.sign(diff) / self.LATENT_DIM
        inp = np.concatenate([z, a_embed])
        self._W_pred -= self.LR * np.outer(grad_pred, inp)
        self._b_pred -= self.LR * grad_pred

        # EMA target encoder update: W_target ← m·W_target + (1-m)·W_enc
        self._W_target = self.EMA_M * self._W_target + (1.0 - self.EMA_M) * self._W_enc
        self._b_target = self.EMA_M * self._b_target + (1.0 - self.EMA_M) * self._b_enc

        self._step += 1
        self._loss_history.append(loss)
        if len(self._loss_history) > 1000:
            self._loss_history = self._loss_history[-1000:]
        return loss

    # ------------------------------------------------------------------
    # ISolver interface
    # ------------------------------------------------------------------

    @property
    def avg_loss(self) -> float:
        """Average L1 world-state prediction loss over the last 20 steps (L_S)."""
        if not self._loss_history:
            return 1.0
        return float(np.mean(self._loss_history[-20:]))

    @property
    def belief_avg_loss(self) -> float:
        """Average L1 belief prediction loss over the last 20 steps (L_B).

        Captures knowledge quality (confidence, epistemic uncertainty, consistency)
        predicted in latent space. Used by epa_loss() as L_B once trained.
        """
        if not self._belief_loss_history:
            return 1.0
        return float(np.mean(self._belief_loss_history[-20:]))

    def _jepa_update_belief(self, S_t: dict, B_t, action: str, B_next) -> float:
        """One online update step for the belief predictor.

        L_B = mean(|z'_B - sg(f̄_B(B_{t+1}))|) + REG_COEFF · relu(1 - std(z'_B))

        Predictor is conditioned on z_S (world latent) + a_embed, not on z_B directly —
        the belief at t+1 is predicted from the world context and action, not from
        the current belief (avoids circular dependence).
        """
        z_S = self._encode(S_t)
        a_embed = self._embed_action(action)
        z_pred_B = self._predict_belief(z_S, a_embed)

        z_target_B = self._encode_belief(B_next, target=True)

        diff = z_pred_B - z_target_B
        loss_jepa = float(np.mean(np.abs(diff)))
        pstd = float(np.std(z_pred_B)) + 1e-8
        loss_reg = max(0.0, 1.0 - pstd)
        loss = loss_jepa + self.REG_COEFF * loss_reg

        grad = np.sign(diff) / self.BELIEF_DIM
        inp = np.concatenate([z_S, a_embed])
        self._W_pred_B -= self.LR * np.outer(grad, inp)
        self._b_pred_B -= self.LR * grad

        self._W_target_B = self.EMA_M * self._W_target_B + (1 - self.EMA_M) * self._W_enc_B
        self._b_target_B = self.EMA_M * self._b_target_B + (1 - self.EMA_M) * self._b_enc_B

        self._belief_step += 1
        self._belief_loss_history.append(loss)
        if len(self._belief_loss_history) > 1000:
            self._belief_loss_history = self._belief_loss_history[-1000:]
        return loss

    def infer(self, state: dict, action: str, belief=None) -> dict:
        """Synchronous inference — no gradient step, no async.

        Called by epa_transition() to get JEPA predicted latents and
        numeric signals for computing Δ_JEPA (the residual).
        Returns plain Python types (no numpy) so epa.py stays import-free.
        """
        z = self._encode(state)
        a_embed = self._embed_action(action)
        z_pred_S = self._predict(z, a_embed)

        result: dict = {
            "latent": z_pred_S.tolist(),
            "latent_signal": float(np.tanh(float(np.mean(z_pred_S)))),
            "avg_loss": self.avg_loss,
            "trained": self._step > 0,
        }

        if belief is not None:
            z_pred_B = self._predict_belief(z, a_embed)
            result["belief_latent_signal"] = float(np.tanh(float(np.mean(z_pred_B))))
            result["belief_avg_loss"] = self.belief_avg_loss
            result["belief_trained"] = self._belief_step > 0

        return result

    def infer_epistemic(
        self,
        S: dict,
        action: str,
        B=None,
        A: set | None = None,
        G=None,
        M: dict | None = None,
    ) -> dict:
        """Unified epistemic inference — predicts full z'_E ∈ R^80.

        Returns the predicted next epistemic state latent and per-component
        signals, so that epa_transition can apply targeted residuals to each
        cognitive component (not just world state).

        Architecture reminder:
          z_E = [z_W | z_B | z_K | z_A | z_G | z_M]
          z'_E = g_E(z_E, a_embed)

          JEPA never sees raw data (pixels, PDFs, audio).
          It sees the epistemic state AFTER modality encoders + evidence fusion.
        """
        z_E = self._encode_epistemic(S, B or object(), A=A, G=G, M=M)
        a_embed = self._embed_action(action)
        z_pred_E = self._predict_epistemic(z_E, a_embed)

        # Split predicted latent back into component slices
        s = 0
        z_pred_W = z_pred_E[s : s + self.LATENT_DIM]
        s += self.LATENT_DIM
        z_pred_B = z_pred_E[s : s + self.BELIEF_DIM]
        s += self.BELIEF_DIM
        z_pred_K = z_pred_E[s : s + self.KNOWLEDGE_DIM]
        s += self.KNOWLEDGE_DIM
        z_pred_A = z_pred_E[s : s + self.AFFORDANCE_DIM]
        s += self.AFFORDANCE_DIM
        z_pred_G = z_pred_E[s : s + self.GOAL_DIM]
        s += self.GOAL_DIM
        z_pred_M = z_pred_E[s : s + self.MESH_DIM]

        avg_e = float(self._epistemic_avg_loss)
        return {
            "z_E": z_pred_E.tolist(),
            "latent_signal_W": float(np.tanh(np.mean(z_pred_W))),
            "latent_signal_B": float(np.tanh(np.mean(z_pred_B))),
            "latent_signal_K": float(np.tanh(np.mean(z_pred_K))),
            "latent_signal_A": float(np.tanh(np.mean(z_pred_A))),
            "latent_signal_G": float(np.tanh(np.mean(z_pred_G))),
            "latent_signal_M": float(np.tanh(np.mean(z_pred_M))),
            "epistemic_avg_loss": avg_e,
            "epistemic_trained": self._epistemic_step > 0,
        }

    @property
    def _epistemic_avg_loss(self) -> float:
        if not self._epistemic_loss_history:
            return 1.0
        return float(np.mean(self._epistemic_loss_history[-20:]))

    def can_solve(self, problem: dict) -> float:
        return 0.75 if problem.get("type") in ("prediction", "world_model") else 0.1

    async def solve(self, problem: dict) -> SolverResult:
        current_state: dict = problem.get("current_state", {})
        action: str = problem.get("action", "")
        history: list = problem.get("history", [])
        next_state: dict | None = problem.get("next_state")  # present during learning

        # --- Online learning pass ---
        last_loss: float | None = None
        if next_state and current_state:
            last_loss = self._jepa_update(current_state, action, next_state)
        elif len(history) >= 2:
            # Learn from consecutive history pairs (last 5 transitions)
            pairs = list(zip(history[-6:-1], history[-5:]))
            for s_t, s_next in pairs:
                if isinstance(s_t, dict) and isinstance(s_next, dict):
                    self._jepa_update(s_t, action, s_next)

        # --- Inference: predict next-state latent ---
        z = self._encode(current_state)
        a_embed = self._embed_action(action)
        z_pred = self._predict(z, a_embed)  # predicted latent z'

        # Approximate next-state dict: JEPA does not have a decoder.
        # We delta-modulate numeric keys in current_state using the predicted latent.
        predicted: dict = dict(current_state)
        if current_state:
            latent_signal = float(np.tanh(float(np.mean(z_pred))))
            for k, v in current_state.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    predicted[k] = round(float(v) + latent_signal * abs(float(v)) * 0.05, 4)

        # Confidence: grows with training steps; discounted by recent loss
        base_conf = min(0.85, 0.45 + 0.01 * min(self._step, 40))
        avg_loss = float(np.mean(self._loss_history[-20:])) if self._loss_history else 1.0
        confidence = float(np.clip(min(base_conf, 1.0 - avg_loss * 0.5), 0.25, 0.85))

        proof = (
            f"JEPA: encoder→predictor→EMA-target | "
            f"steps={self._step} avg_L1={avg_loss:.4f} "
            f"latent_dim={self.LATENT_DIM} ema_m={self.EMA_M}"
        )
        if last_loss is not None:
            proof += f" last_loss={last_loss:.4f}"

        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={
                "predicted_state": predicted,
                "action": action,
                "latent": z_pred.tolist(),
            },
            confidence=confidence,
            proof=proof,
        )
