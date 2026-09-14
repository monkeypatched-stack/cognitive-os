"""File Service — minimal stub for development."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="File Service", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "file", "mode": "stub"}


@app.get("/")
async def root():
    return {"service": "file", "status": "stub — not yet implemented"}
