from pydantic import BaseModel, Field


class SimpleTodoListServiceItemCreate(BaseModel):
    description: str = Field(..., min_length=1)
    status: str = Field(..., regex="^(pending|completed)$")


class SimpleTodoListServiceItemUpdate(BaseModel):
    description: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(None, regex="^(pending|completed)$")


class SimpleTodoListServiceItemResponse(BaseModel):
    reference: str
    description: str
    status: str
    completed_at: Optional[str] = None


class SimpleTodoListServiceItemListResponse(BaseModel):
    items: List[SimpleTodoListServiceItemResponse]


class TodoListServiceReference(BaseModel):
    id: str


class TodoListServiceCreatedEvent(BaseModel):
    reference: TodoListServiceReference


class TodoListServiceUpdatedEvent(BaseModel):
    reference: TodoListServiceReference


class TodoListServiceDeletedEvent(BaseModel):
    reference: TodoListServiceReference
