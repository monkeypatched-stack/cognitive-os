from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.helpers.me import get_current_user
from services.auth.models.users import UserResponse
from services.auth.helpers.users import get_by_id
from services.common.db import get_database

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------
@router.get("", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_me(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the authenticated user's profile.
    Requires `Authorization: Bearer <access_token>` header.
    """
    user = await get_by_id(db, current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return UserResponse(**user)
