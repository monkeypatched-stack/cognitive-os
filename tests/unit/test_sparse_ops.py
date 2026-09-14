"""Sparse arithmetic layer over the world tensor (ADR 0021).

Covers the general primitives (add/sub/scale/hadamard/mask/matmul/propagate) and two
concrete, structurally-different scenarios — proving the layer is not tied to any one
domain: (1) grocery fulfilment across a person's and a store's LOCAL belief matrices,
(2) a manufacturing order. Same primitives, different content.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    Feature,
    SparseMatrix,
    SparseTransitionTensor,
    epistemic_loss,
)

# ── general arithmetic (must generalize, not be milk-specific) ───────────────────


def test_elementwise_add_sub_scale():
    A = SparseMatrix({("a", "b"): 1.0, ("b", "c"): 2.0})
    B = SparseMatrix({("a", "b"): 0.5, ("c", "d"): 1.0})
    assert (A + B).get("a", "b") == 1.5
    assert (A + B).get("c", "d") == 1.0
    assert (A - B).get("a", "b") == 0.5
    assert A.scale(2.0).get("b", "c") == 4.0
    assert A.hadamard(B).get("a", "b") == 0.5  # 1.0 * 0.5
    assert A.hadamard(B).get("b", "c") == 0.0  # no overlap


def test_mask_is_constraint_injection():
    A = SparseMatrix({("x", "y"): 0.9, ("x", "z"): 0.2})
    kept = A.mask(lambda r, c, v: v >= 0.5)  # drop low-weight transitions
    assert kept.get("x", "y") == 0.9 and kept.get("x", "z") == 0.0


def test_matmul_composition():
    # (A @ B)[i,k] = Σ_j A[i,j] B[j,k]
    A = SparseMatrix({("i", "j"): 2.0})
    B = SparseMatrix({("j", "k"): 3.0})
    assert A.matmul(B).get("i", "k") == 6.0


def test_epistemic_loss_is_operator_distance():
    A = SparseMatrix({("s", "t"): 1.0})
    assert epistemic_loss(A, A) == 0.0  # identical → 0
    B = SparseMatrix({("s", "t"): 0.0, ("s", "u"): 1.0})  # completely different edge
    assert epistemic_loss(A, B) > 0.0


# ── scenario 1: grocery fulfilment across two LOCAL belief matrices ──────────────


def test_two_actor_grocery_fulfilment():
    # PERSON's local beliefs: taking the action puts milk on their list
    person = SparseMatrix()
    person.add_at("AddMilk", "MilkOnList", 1.0)  # each action is a local transition

    # STORE's (Instacart) local beliefs: a requested item flows to delivery
    store = SparseMatrix()
    for a, b in [
        ("MilkRequested", "InCart"),
        ("InCart", "OrderPlaced"),
        ("OrderPlaced", "PaymentMade"),
        ("PaymentMade", "Delivered"),
    ]:
        store.add_at(a, b, 1.0)

    # COMMUNICATION link: the person's list is seen by the store as a request
    bridge = SparseMatrix({("MilkOnList", "MilkRequested"): 1.0})

    # the shared world is the sum of the two local belief matrices + the link
    world = person + bridge + store

    # one action — "add milk" — then the operator propagates the rest automatically
    trace = world.propagate_k({"AddMilk": 1.0}, k=6)
    reached = [max(v, key=v.get) for v in trace if v]  # dominant state at each hop
    assert reached == [
        "AddMilk",
        "MilkOnList",
        "MilkRequested",
        "InCart",
        "OrderPlaced",
        "PaymentMade",
        "Delivered",
    ]
    # the person's local matrix knows nothing of payment; the store's knows nothing of
    # the grocery list — two subjective views, joined only by the communication link
    assert person.get("OrderPlaced", "PaymentMade") == 0.0
    assert store.get("AddMilk", "MilkOnList") == 0.0


def test_belief_divergence_between_person_and_store():
    # the person believes adding milk means it's "done"; the store believes it still
    # needs payment. epistemic_loss measures that divergence — a measurement, not a fix.
    person = SparseMatrix({("MilkOnList", "Delivered"): 1.0})  # naive belief: list → done
    store = SparseMatrix({("MilkOnList", "InCart"): 1.0})  # realistic: list → cart
    assert epistemic_loss(person, store) > 0.0


# ── scenario 2: a structurally different domain (proves generalization) ──────────


def test_manufacturing_order_same_primitives():
    world = SparseMatrix()
    for a, b in [
        ("Order", "Produce"),
        ("Produce", "QC"),
        ("QC", "Ship"),
        ("Ship", "Invoice"),
        ("Invoice", "Paid"),
    ]:
        world.add_at(a, b, 1.0)
    trace = world.propagate_k({"Order": 1.0}, k=5)
    assert list(trace[-1]) == ["Paid"]  # order → … → paid
    assert list(trace[3]) == ["Ship"]  # third hop is Ship


# ── adapters: the layer plugs onto the tensor and the compiled operator ─────────


def test_from_tensor_and_operator_adapters():
    W = SparseTransitionTensor()
    W.observe("A", "B", domain="d")
    W.observe("B", "C", domain="d")
    m_tensor = SparseMatrix.from_tensor(W, Feature.PROBABILITY)
    assert m_tensor.get("A", "B") == 1.0
    op = W.to_operator(domain="d", f=Feature.PROBABILITY)
    m_op = SparseMatrix.from_operator(op)
    assert m_op.get("A", "B") == 1.0
    # propagation over the compiled operator reaches C in two hops
    assert list(m_op.propagate_k({"A": 1.0}, 2)[-1]) == ["C"]
