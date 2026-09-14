from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.dto import WriteBlogOnCognitionItemDTO
from infrastructure.database import get_async_db
from domain.repository import WriteBlogOnCognitionRepository
from domain.aggregate import WriteBlogOnCognitionAggregateRoot


async def get_write_blog_on_cognition_item_by_id(
    query: int,
    repository: WriteBlogOnCognitionRepository = Depends(
        WriteBlogOnCognitionRepository
    ),
):
    write_blog_on_cognition_item = (
        await repository.get_write_blog_on_cognition_item_by_id(item_id=query)
    )
    if not write_blog_on_cognition_item:
        raise HTTPException(
            status_code=404, detail="Write Blog On Cognition item not found"
        )
    return WriteBlogOnCognitionItemDTO.from_value(
        write_blog_on_cognition_item.to_dict()
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
