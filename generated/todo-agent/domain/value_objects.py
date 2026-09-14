from typing import Optional, List


class Status:
    def __init__(self, value: str):
        self.value = value

    def is_valid(self) -> bool:
        return self.value in ["pending", "completed"]


class Reference:
    def __init__(self, id: str):
        self.id = id
