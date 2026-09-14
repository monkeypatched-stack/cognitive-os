from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from domain.aggregate import WriteBlogOnCognitionAggregateRoot
from domain.repository import WriteBlogOnCognitionRepository
from infrastructure.persistence.motor import (
    MotorWriteBlogOnCognitionRepository,
    MongoDBRepository,
)
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
from api.routes import router as write_blog_on_cognition_router

app = FastAPI()

# CORS settings
origins = [
    "http://localhost",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sentry configuration
sentry_sdk.init(dsn="your_sentry_dsn", integrations=[FastApiIntegration()])


# Database connection setup
async def get_async_db():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db_repository = MongoDBRepository(client)
    motor_repo = MotorWriteBlogOnCognitionRepository(
        await db_repository.get_connection()
    )
    yield motor_repo
    await client.close()


# Dependency for current user (JWT authentication)
async def get_current_user(token: str):
    # Implement JWT validation logic here
    pass


# API routes setup
app.include_router(write_blog_on_cognition_router, prefix="/api")


# Lifespan events
@app.on_event("startup")
async def startup():
    app.state.db = await get_async_db()


@app.on_event("shutdown")
async def shutdown():
    await app.state.db.close()


# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}
