from typing import Optional
from pydantic import BaseModel, Field


class ReferenceDTO(BaseModel):
    reference_id: str = Field(..., min_length=1)


class StatusDTO(str, Enum):
    draft = "draft"
    published = "published"


class WriteBlogPostAboutItemCreateDTO(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    reference: ReferenceDTO
    status: StatusDTO = Field(default=StatusDTO.draft)


class WriteBlogPostAboutItemUpdateDTO(BaseModel):
    title: Optional[str]
    content: Optional[str]
    reference: Optional[ReferenceDTO]
    status: Optional[StatusDTO]


class WriteBlogPostAboutItemResponseDTO(BaseModel):
    blog_post_id: str
    title: str
    content: str
    reference: ReferenceDTO
    status: StatusDTO
