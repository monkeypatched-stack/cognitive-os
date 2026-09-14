from fastapi import APIRouter

router = APIRouter()


@router.get("/capabilities")
async def capabilities() -> dict:
    return {
        "service": "warehouse_execution",
        "capabilities": [
            "warehouse_task_execution",
            "worker_assignment",
            "scanner_validation",
            "pick_pack_execution",
            "putaway_execution",
            "movement_execution",
            "exception_resolution",
        ],
        "status": "scaffolded",
    }
