from typing import Any
from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    agent_type: str = Field(..., min_length=1, max_length=128)
    # RBAC: role_ids are looked up against the existing roles collection.
    # The union of their permission_ids becomes the agent's token scopes.
    role_ids: list[str] = Field(default_factory=list)
    # Explicit scopes as a fallback when no roles are assigned.
    scopes: list[str] = Field(default_factory=lambda: ["read:agents"])
    # SPIFFE URI — auto-generated if omitted.
    spiffe_id: str | None = Field(default=None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegisterResponse(BaseModel):
    agent_id: str  # canonical SPIFFE URI
    client_id: str
    client_secret: str  # returned once; store it securely
    agent_type: str
    role_ids: list[str]
    scopes: list[str]
    spiffe_id: str


class AgentTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    scope: str


class AgentRefreshRequest(BaseModel):
    grant_type: str = Field(..., pattern="^refresh_token$")
    refresh_token: str = Field(..., min_length=1)
