from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from application.dto import WriteBlogOnCognitionItemDTO
from infrastructure.database import get_async_db
from domain.aggregate import WriteBlogOnCognitionAggregateRoot
from domain.repository import WriteBlogOnCognitionRepository
from application.commands import (
    CreateWriteBlogOnCognitionCommand,
    UpdateWriteBlogOnCognitionCommand,
    DeleteWriteBlogOnCognitionCommand,
)


async def create_write_blog_on_cognition_command_handler(
    command: CreateWriteBlogOnCognitionCommand,
    repository: WriteBlogOnCognitionRepository,
):
    write_blog_on_cognition_item = command.write_blog_on_cognition_item
    await repository.add_write_blog_on_cognition_item(write_blog_on_cognition_item)
    return {"message": "Write Blog On Cognition created successfully"}


async def update_write_blog_on_cognition_command_handler(
    command: UpdateWriteBlogOnCognitionCommand,
    repository: WriteBlogOnCognitionRepository,
):
    write_blog_on_cognition_item = command.write_blog_on_cognition_item
    await repository.update_write_blog_on_cognition_item(write_blog_on_cognition_item)
    return {"message": "Write Blog On Cognition updated successfully"}


async def delete_write_blog_on_cognition_command_handler(
    command: DeleteWriteBlogOnCognitionCommand,
    repository: WriteBlogOnCognitionRepository,
):
    item_id = command.item_id
    await repository.delete_write_blog_on_cognition_item(item_id)
    return {"message": "Write Blog On Cognition deleted successfully"}
