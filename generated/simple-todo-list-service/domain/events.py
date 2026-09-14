from typing import Optional


class SimpleTodoListServiceCreated:
    def __init__(self, reference: "Reference"):
        self.reference = reference


class SimpleTodoListServiceUpdated:
    def __init__(self, reference: "Reference"):
        self.reference = reference


class SimpleTodoListServiceDeleted:
    def __init__(self, reference: "Reference"):
        self.reference = reference
