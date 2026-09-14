from fastapi import HTTPException

from application.dto import TodoAgentDTO, TodoAgentItemDTO
from application.exceptions import TodoAgentNotFoundException
from application.services import TodoAgentService
from domain.entities import TodoAgent
from infrastructure.repositories import MotorTodoAgentRepository


class CreateTodoAgentCommand:
    def __init__(self, todo_agent_dto: TodoAgentDTO):
        self.todo_agent_dto = todo_agent_dto


class UpdateTodoAgentCommand:
    def __init__(self, todo_agent_id: int, todo_agent_dto: TodoAgentDTO):
        self.todo_agent_id = todo_agent_id
        self.todo_agent_dto = todo_agent_dto


class DeleteTodoAgentCommand:
    def __init__(self, todo_agent_id: int):
        self.todo_agent_id = todo_agent_id


class TodoAgentCommandHandler:
    def __init__(self, repository: MotorTodoAgentRepository):
        self.repository = repository
        self.service = TodoAgentService()

    async def handle_create(self, command: CreateTodoAgentCommand) -> TodoAgentDTO:
        todo_agent = await self.service.create_todo_agent(command.todo_agent_dto)
        return TodoAgentDTO.from_domain(todo_agent)

    async def handle_update(self, command: UpdateTodoAgentCommand) -> TodoAgentDTO:
        try:
            updated_todo_agent = await self.service.update_todo_agent(
                command.todo_agent_id, command.todo_agent_dto
            )
            return TodoAgentDTO.from_domain(updated_todo_agent)
        except TodoAgentNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))

    async def handle_delete(self, command: DeleteTodoAgentCommand) -> None:
        try:
            await self.service.delete_todo_agent(command.todo_agent_id)
        except TodoAgentNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
