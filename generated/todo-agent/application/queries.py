from typing import List, Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from domain.entities import TodoAgentItem, Status, Reference, TodoAgent
from domain.value_objects import Status, Reference
from domain.aggregate import TodoAgentAggregate
from domain.repository import TodoAgentRepository
from application.dto import TodoAgentDTO, TodoAgentItemDTO
from application.exceptions import RepositoryConflict, RepositoryNotFound
from infrastructure.database import get_db_session
from infrastructure.persistence.motor import MotorTodoAgentRepository
from dependencies import get_current_user


class GetTodoAgentQuery:
    def __init__(self, todo_agent_repository: TodoAgentRepository):
        self.todo_agent_repository = todo_agent_repository

    async def execute(self, todo_agent_id: str) -> TodoAgentDTO:
        todo_agent = await self.todo_agent_repository.get(todo_agent_id)
        if not todo_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Todo Agent not found"
            )
        return TodoAgentDTO.from_domain(todo_agent)


class ListTodoAgentsQuery:
    def __init__(self, todo_agent_repository: TodoAgentRepository):
        self.todo_agent_repository = todo_agent_repository

    async def execute(self) -> List[TodoAgentDTO]:
        todo_agents = await self.todo_agent_repository.list()
        return [TodoAgentDTO.from_domain(todo_agent) for todo_agent in todo_agents]


class GetTodoAgentItemQuery:
    def __init__(self, todo_agent_repository: TodoAgentRepository):
        self.todo_agent_repository = todo_agent_repository

    async def execute(self, todo_agent_id: str, item_id: str) -> TodoAgentItemDTO:
        todo_agent = await self.todo_agent_repository.get(todo_agent_id)
        if not todo_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Todo Agent not found"
            )
        item = next((item for item in todo_agent.items if item.id == item_id), None)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Todo Agent Item not found",
            )
        return TodoAgentItemDTO.from_domain(item)


class ListTodoAgentItemsQuery:
    def __init__(self, todo_agent_repository: TodoAgentRepository):
        self.todo_agent_repository = todo_agent_repository

    async def execute(self, todo_agent_id: str) -> List[TodoAgentItemDTO]:
        todo_agent = await self.todo_agent_repository.get(todo_agent_id)
        if not todo_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Todo Agent not found"
            )
        return [TodoAgentItemDTO.from_domain(item) for item in todo_agent.items]
