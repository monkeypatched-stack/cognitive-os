from typing import Optional
from enum import Enum


class Reference:
    def __init__(self, id: str):
        if not isinstance(id, str) or not id:
            raise ValueError("Reference ID must be a non-empty string")
        self.id = id


class Status(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"

    @classmethod
    def from_str(cls, value: str) -> "Status":
        try:
            return cls(value.upper())
        except ValueError:
            raise ValueError(f"Invalid status value: {value}")
