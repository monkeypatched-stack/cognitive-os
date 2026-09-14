from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

InventoryStatus = Literal[
    "In-Stock",
    "Low-Stock",
    "Out-of-Stock",
    "Reserved",
    "In-Transit",
    "Quarantine",
    "Damaged",
    "Expired",
]

InventoryTransactionType = Literal[
    "Receipt",
    "Shipment",
    "Adjustment",
    "Transfer-In",
    "Transfer-Out",
    "Return",
    "Write-Off",
    "Cycle-Count",
    "Reservation",
    "Release",
]

StockMovementDirection = Literal["in", "out", "transfer", "adjustment"]

InventoryValuationMethod = Literal[
    "FIFO",
    "LIFO",
    "Weighted-Average",
    "Specific-Identification",
]

InventoryLocationType = Literal[
    "Warehouse", "Store", "Transit", "Virtual", "Production"
]

ReferenceType = Literal[
    "PO",
    "SO",
    "Transfer",
    "Adjustment",
    "Production",
    "Return",
    "Work-Order",
]

AdjustmentType = Literal["Add", "Remove", "Correction"]
AdjustmentStatus = Literal["Pending", "Approved", "Rejected", "Completed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
