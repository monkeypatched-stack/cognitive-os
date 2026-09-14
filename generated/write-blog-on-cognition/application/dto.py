from pydantic import BaseModel, Field
from typing import Optional, List


class ReferenceDTO(BaseModel):
    url: str = Field(...)


class WriteBlogOnCognitionItemDTO(BaseModel):
    id: int = Field(...)
    title: str = Field(...)
    content: str = Field(...)
    status: str = Field(...)
    references: Optional[List[ReferenceDTO]] = Field(default_factory=list)
