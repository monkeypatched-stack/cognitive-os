from fastapi import FastAPI, Depends, HTTPException
from lemon import logger
from sentry_sdk import capture_exception
from opentelemetry import trace

from infrastructure.database import MongoDBRepository
from api.dependencies import get_current_user, get_db
from application.commands import CreateItemCommand, UpdateItemStatusCommand
from application.queries import GetItemsQuery

app = FastAPI()

tracer = trace.get_tracer(__name__)


@app.on_event("startup")
async def startup_event():
    logger.info("Starting Simple Todo List Service API")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Simple Todo List Service API")


@app.post("/todo-list-service/{todo_list_service_id}/items/", response_model=None)
async def create_item(
    todo_list_service_id: str,
    item_data: CreateItemCommand.ItemCreate,
    current_user=Depends(get_current_user),
    db: MongoDBRepository = Depends(get_db),
):
    with tracer.start_as_current_span("create-item"):
        try:
            command = CreateItemCommand(repository=db)
            await command.execute(Reference(id=todo_list_service_id), item_data)
            logger.info(f"Created item for todo list service {todo_list_service_id}")
        except HTTPException as e:
            capture_exception(e)
            raise


@app.put(
    "/todo-list-service/{todo_list_service_id}/items/{item_id}/status/",
    response_model=None,
)
async def update_item_status(
    todo_list_service_id: str,
    item_id: str,
    status_data: UpdateItemStatusCommand.ItemUpdate,
    current_user=Depends(get_current_user),
    db: MongoDBRepository = Depends(get_db),
):
    with tracer.start_as_current_span("update-item-status"):
        try:
            command = UpdateItemStatusCommand(repository=db)
            await command.execute(
                Reference(id=todo_list_service_id),
                Reference(id=item_id),
                status_data.status,
            )
            logger.info(
                f"Updated item status for todo list service {todo_list_service_id}"
            )
        except HTTPException as e:
            capture_exception(e)
            raise


@app.get("/todo-list-service/{todo_list_service_id}/items/", response_model=None)
async def get_items(
    todo_list_service_id: str,
    current_user=Depends(get_current_user),
    db: MongoDBRepository = Depends(get_db),
):
    with tracer.start_as_current_span("get-items"):
        try:
            query = GetItemsQuery(repository=db)
            items = await query.execute(Reference(id=todo_list_service_id))
            logger.info(f"Retrieved items for todo list service {todo_list_service_id}")
            return {"items": items}
        except HTTPException as e:
            capture_exception(e)
            raise
