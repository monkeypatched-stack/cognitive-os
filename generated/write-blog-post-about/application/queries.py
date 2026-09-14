from typing import List, Optional

from fastapi import HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.domain import repository
from app.application.dto import WriteBlogPostAboutItemResponseDTO


class GetBlogPostQueryDTO(BaseModel):
    blog_post_id: str = Field(..., min_length=1)


async def get_blog_post(blog_post_id: str, db: Session = Depends(get_db)):
    try:
        blog_post_item = repository.WriteBlogPostAboutRepository(db).find_by_id(
            blog_post_id
        )
        if not blog_post_item:
            raise HTTPException(status_code=404, detail="Blog post not found")

        return WriteBlogPostAboutItemResponseDTO(
            blog_post_id=blog_post_item.blog_post_id,
            title=blog_post_item.title,
            content=blog_post_item.content,
            reference={"reference_id": blog_post_item.reference.reference_id},
            status=blog_post_item.status.value,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ListBlogPostsQueryDTO(BaseModel):
    pass


async def list_blog_posts(
    query: ListBlogPostsQueryDTO = None, db: Session = Depends(get_db)
):
    try:
        blog_post_items = repository.WriteBlogPostAboutRepository(db).find_all()
        return [
            WriteBlogPostAboutItemResponseDTO(
                blog_post_id=blog_post_item.blog_post_id,
                title=blog_post_item.title,
                content=blog_post_item.content,
                reference={"reference_id": blog_post_item.reference.reference_id},
                status=blog_post_item.status.value,
            )
            for blog_post_item in blog_post_items
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
