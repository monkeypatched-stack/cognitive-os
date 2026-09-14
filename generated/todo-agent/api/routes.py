from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_current_user, get_db
from application.commands import (
    CreateTodoAgentCommand,
    UpdateTodoAgentCommand,
    DeleteTodoAgentCommand,
)
from application.handlers import TodoAgentCommandHandler
from infrastructure.repositories import MotorTodoAgentRepository

router = APIRouter()


@router.post("/todo-agents/", response_model=TodoAgentDTO)
async def create_todo_agent(
    todo_agent_dto: TodoAgentDTO, repository: MotorTodoAgentRepository = Depends()
):
    command_handler = TodoAgentCommandHandler(repository)
    return await command_handler.handle_create(CreateTodoAgentCommand(todo_agent_dto))


@router.put("/todo-agents/{todo_agent_id}", response_model=TodoAgentDTO)
async def update_todo_agent(
    todo_agent_id: int,
    todo_agent_dto: TodoAgentDTO,
    repository: MotorTodoAgentRepository = Depends(),
):
    command_handler = TodoAgentCommandHandler(repository)
    return await command_handler.handle_update(
        UpdateTodoAgentCommand(todo_agent_id, todo_agent_dto)
    )


@router.delete("/todo-agents/{todo_agent_id}", status_code=204)
async def delete_todo_agent(
    todo_agent_id: int, repository: MotorTodoAgentRepository = Depends()
):
    command_handler = TodoAgentCommandHandler(repository)
    await command_handler.handle_delete(DeleteTodoAgentCommand(todo_agent_id))
