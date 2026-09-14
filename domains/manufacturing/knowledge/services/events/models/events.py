from typing import Literal, Optional
from pydantic import BaseModel

EventCategory = Literal[
    "Event Log",
    "Count Event",
    "Maintenance Log",
    "Downtime Log",
    "Cleaning Log",
    "Calibration Log",
]

DiaEventMode = Literal["add", "edit", "delete"]

FilterTab = Literal[
    "all",
    "Maintenance Log",
    "Downtime Log",
    "Cleaning Log",
    "Calibration Log",
]


class EventResponse(BaseModel):
    id: str
    scope: Optional[str] = None
    date: str
    time: str
    type: str
    category: EventCategory
    work_order_id: str
    priority: str
    equipment_id: Optional[str] = None
    machine_id: Optional[str] = None
    details: str
    user: str
    nfc_type: str
    nfc_tag_id: str
    nfc_max_size: int
    nfc_payload: str

    class Config:
        from_attributes = True


class PaginatedEventsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[EventResponse]
