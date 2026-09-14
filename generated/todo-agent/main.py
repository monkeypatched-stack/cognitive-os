from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.routes import router as todo_agent_router
from domain.exceptions import TodoAgentNotFoundException
from infrastructure.repositories import MotorTodoAgentRepository
from infrastructure.database import AsyncIOMotorClient

app = FastAPI()


@app.on_event("startup")
async def startup():
    app.state.sentry_sdk_client.capture_exception(
        HTTPException(status_code=500, detail="Startup error")
    )


@app.get("/health", status_code=200)
def health_check():
    return {"status": "OK"}


@app.middleware("http")
async def timing_middleware(request, call_next):
    import time

    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


@app.on_event("shutdown")
async def shutdown():
    app.state.sentry_sdk_client.capture_exception(
        HTTPException(status_code=500, detail="Shutdown error")
    )


@app.post("/todo-agents/", response_model=TodoAgentDTO)
async def create_todo_agent(
    todo_agent_dto: TodoAgentDTO, repository: MotorTodoAgentRepository = Depends()
):
    command_handler = TodoAgentCommandHandler(repository)
    return await command_handler.handle_create(CreateTodoAgentCommand(todo_agent_dto))


@app.put("/todo-agents/{todo_agent_id}", response_model=TodoAgentDTO)
async def update_todo_agent(
    todo_agent_id: int,
    todo_agent_dto: TodoAgentDTO,
    repository: MotorTodoAgentRepository = Depends(),
):
    command_handler = TodoAgentCommandHandler(repository)
    return await command_handler.handle_update(
        UpdateTodoAgentCommand(todo_agent_id, todo_agent_dto)
    )


@app.delete("/todo-agents/{todo_agent_id}", status_code=204)
async def delete_todo_agent(
    todo_agent_id: int, repository: MotorTodoAgentRepository = Depends()
):
    command_handler = TodoAgentCommandHandler(repository)
    await command_handler.handle_delete(DeleteTodoAgentCommand(todo_agent_id))
