from typing import Optional


class TodoAgentCreatedEvent:
    def __init__(self, todo_agent_id: str):
        self.todo_agent_id = todo_agent_id


class TodoAgentUpdatedEvent:
    def __init__(self, todo_agent_id: str, item_id: Optional[str] = None):
        self.todo_agent_id = todo_agent_id
        self.item_id = item_id


class TodoAgentDeletedEvent:
    def __init__(self, todo_agent_id: str):
        self.todo_agent_id = todo_agent_id
