"""Intent examples for the embedding classifier — trained with 100k grounded manufacturing data.

Split into multiple submodules (each < 500 lines) to keep files under the
architecture validator's size limits — the original single 9109-line file is
gone, but the public contract is unchanged: `INTENT_EXAMPLES` still exposes
the same dict[str, list[str]], importable from this same module path.
"""

from __future__ import annotations

from . import (
    _misc_small,
    _change_control,
    _change_control_query,
    _sop_compliance,
    _warehouse_and_work_order_create,
    _worker_1,
    _worker_2,
    _approval_query_1,
    _approval_query_2,
    _batch_record_1,
    _batch_record_2,
    _batch_record_3,
    _batch_record_4,
    _batch_record_5,
    _batch_record_6,
    _batch_record_7,
    _work_order_query_1,
    _work_order_query_2,
    _work_order_query_3,
    _work_order_query_4,
    _work_order_query_5,
)

_FRAGMENTS = (
    _misc_small.EXAMPLES,
    _change_control.EXAMPLES,
    _change_control_query.EXAMPLES,
    _sop_compliance.EXAMPLES,
    _warehouse_and_work_order_create.EXAMPLES,
    _worker_1.EXAMPLES,
    _worker_2.EXAMPLES,
    _approval_query_1.EXAMPLES,
    _approval_query_2.EXAMPLES,
    _batch_record_1.EXAMPLES,
    _batch_record_2.EXAMPLES,
    _batch_record_3.EXAMPLES,
    _batch_record_4.EXAMPLES,
    _batch_record_5.EXAMPLES,
    _batch_record_6.EXAMPLES,
    _batch_record_7.EXAMPLES,
    _work_order_query_1.EXAMPLES,
    _work_order_query_2.EXAMPLES,
    _work_order_query_3.EXAMPLES,
    _work_order_query_4.EXAMPLES,
    _work_order_query_5.EXAMPLES,
)


def _merge(fragments) -> dict[str, list[str]]:
    """Concatenate example lists for keys that were split across multiple parts."""
    merged: dict[str, list[str]] = {}
    for frag in fragments:
        for key, examples in frag.items():
            merged.setdefault(key, []).extend(examples)
    return merged


INTENT_EXAMPLES: dict[str, list[str]] = _merge(_FRAGMENTS)
