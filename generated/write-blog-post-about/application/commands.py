from typing import Optional
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.domain import domain
from app.infrastructure import repositories
from app.application.dto import (
    WriteBlogPostAboutItemCreateDTO,
    WriteBlogPostAboutItemUpdateDTO,
    WriteBlogPostAboutItemResponseDTO,
)
from app.application.exceptions import BlogPostNotFoundException
from app.infrastructure.database import get_db
from app.dependencies import get_current_user

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def create_blog_post(
    blog_post_item: WriteBlogPostAboutItemCreateDTO,
    db: Session = Depends(get_db),
    current_user: domain.User = Depends(get_current_user),
):
    try:
        reference = repositories.ReferenceRepository(db).find_by_id(
            blog_post_item.reference.reference_id
        )
        if not reference:
            raise BlogPostNotFoundException("Reference not found")

        new_blog_post = domain.WriteBlogPostAboutItem(
            title=blog_post_item.title,
            content=blog_post_item.content,
            reference=reference,
            status=domain.Status.from_string(blog_post_item.status),
        )
        created_event = repositories.WriteBlogPostAboutRepository(db).create(
            new_blog_post
        )

        return WriteBlogPostAboutItemResponseDTO(
            blog_post_id=new_blog_post.blog_post_id,
            title=new_blog_post.title,
            content=new_blog_post.content,
            reference=WriteBlogPostAboutItemReferenceDTO(
                reference_id=new_blog_post.reference.reference_id
            ),
            status=new_blog_post.status.value,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def update_blog_post(
    blog_post_id: str,
    blog_post_item_update_dto: WriteBlogPostAboutItemUpdateDTO,
    db: Session = Depends(get_db),
    current_user: domain.User = Depends(get_current_user),
):
    try:
        existing_blog_post = repositories.WriteBlogPostAboutRepository(db).find_by_id(
            blog_post_id
        )
        if not existing_blog_post:
            raise BlogPostNotFoundException("Blog post not found")

        if blog_post_item_update_dto.title is not None:
            existing_blog_post.title = blog_post_item_update_dto.title
        if blog_post_item_update_dto.content is not None:
            existing_blog_post.update_content(blog_post_item_update_dto.content)
        if blog_post_item_update_dto.reference is not None:
            reference = repositories.ReferenceRepository(db).find_by_id(
                blog_post_item_update_dto.reference.reference_id
            )
            if not reference:
                raise BlogPostNotFoundException("Reference not found")
            existing_blog_post.reference = reference
        if blog_post_item_update_dto.status is not None:
            existing_blog_post.status = domain.Status.from_string(
                blog_post_item_update_dto.status
            )

        updated_event = repositories.WriteBlogPostAboutRepository(db).update(
            existing_blog_post
        )

        return WriteBlogPostAboutItemResponseDTO(
            blog_post_id=existing_blog_post.blog_post_id,
            title=existing_blog_post.title,
            content=existing_blog_post.content,
            reference=WriteBlogPostAboutItemReferenceDTO(
                reference_id=existing_blog_post.reference.reference_id
            ),
            status=existing_blog_post.status.value,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def delete_blog_post(
    blog_post_id: str,
    db: Session = Depends(get_db),
    current_user: domain.User = Depends(get_current_user),
):
    try:
        existing_blog_post = repositories.WriteBlogPostAboutRepository(db).find_by_id(
            blog_post_id
        )
        if not existing_blog_post:
            raise BlogPostNotFoundException("Blog post not found")

        deleted_event = repositories.WriteBlogPostAboutRepository(db).delete(
            blog_post_id
        )

        return {"detail": "Blog post deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
