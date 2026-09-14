from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.dto import WriteBlogOnCognitionItemDTO
from infrastructure.database import get_async_db
from domain.repository import WriteBlogOnCognitionRepository
from application.commands import (
    CreateWriteBlogOnCognitionCommand,
    UpdateWriteBlogOnCognitionCommand,
    DeleteWriteBlogOnCognitionCommand,
)
from application.handlers import (
    create_write_blog_on_cognition_handler,
    update_write_blog_on_cognition_handler,
    delete_write_blog_on_cognition_handler,
)

router = APIRouter()


@router.post("/write-blog-on-cognition/", response_model=WriteBlogOnCognitionItemDTO)
async def create_write_blog_on_cognition(
    item: WriteBlogOnCognitionItemDTO,
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    command = CreateWriteBlogOnCognitionCommand(write_blog_on_cognition_item=item)
    return await create_write_blog_on_cognition_handler(command, repository)


@router.put(
    "/write-blog-on-cognition/{item_id}", response_model=WriteBlogOnCognitionItemDTO
)
async def update_write_blog_on_cognition(
    item_id: int,
    item: WriteBlogOnCognitionItemDTO,
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    command = UpdateWriteBlogOnCognitionCommand(
        item_id=item_id, write_blog_on_cognition_item=item
    )
    return await update_write_blog_on_cognition_handler(command, repository)


@router.delete("/write-blog-on-cognition/{item_id}", response_model=dict)
async def delete_write_blog_on_cognition(
    item_id: int,
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    command = DeleteWriteBlogOnCognitionCommand(item_id=item_id)
    return await delete_write_blog_on_cognition_handler(command, repository)


@router.get(
    "/write-blog-on-cognition/{item_id}", response_model=WriteBlogOnCognitionItemDTO
)
async def get_write_blog_on_cognition_item(
    item_id: int,
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    write_blog_on_cognition_item = (
        await repository.get_write_blog_on_cognition_item_by_id(item_id=item_id)
    )
    if not write_blog_on_cognition_item:
        raise HTTPException(
            status_code=404, detail="Write Blog On Cognition item not found"
        )
    return WriteBlogOnCognitionItemDTO.from_value(
        write_blog_on_cognition_item.to_dict()
    )


@router.get(
    "/write-blog-on-cognition/", response_model=list[WriteBlogOnCognitionItemDTO]
)
async def list_write_blog_on_cognition_items(
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    write_blog_on_cognition_items = (
        await repository.list_write_blog_on_cognition_items()
    )
    return [
        WriteBlogOnCognitionItemDTO.from_value(item.to_dict())
        for item in write_blog_on_cognition_items
    ]
