from fastapi import APIRouter

router = APIRouter()


@router.get("/capabilities")
async def capabilities() -> dict:
    return {
        "service": "supply_allocation",
        "capabilities": [
            "available_to_promise",
            "available_to_allocate",
            "reservation_commitment",
            "demand_arbitration",
            "allocation_policy",
            "shortage_detection",
        ],
        "status": "scaffolded",
    }
