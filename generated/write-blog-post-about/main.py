from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.domain import domain
from app.infrastructure import repositories, persistence
from app.application import (
    commands,
    queries,
)
from app.dependencies import get_current_user, get_db
from app.api.routers import blog_posts_router

app = FastAPI()


# Middleware for logging and observability
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log request details using Lemon structured logger
    response = await call_next(request)
    return response


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    # Emit OpenTelemetry spans for all requests
    with tracer.start_as_current_span(f"request_{request.method} {request.url}"):
        response = await call_next(request)
        return response


# Dependency to get MongoDB client
@app.on_event("startup")
async def startup_db_client():
    app.mongodb_client = await persistence.get_mongodb_client()


@app.on_event("shutdown")
async def shutdown_db_client():
    await app.mongodb_client.close()


# API routes
app.include_router(blog_posts_router, prefix="/api/v1/blog-posts")


# Health check endpoint
@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "healthy"}
