from fastapi import APIRouter

router = APIRouter()


@router.get("/capabilities")
async def capabilities() -> dict:
    return {
        "service": "replenishment",
        "capabilities": [
            "reorder_point_planning",
            "safety_stock_monitoring",
            "lead_time_planning",
            "purchase_proposals",
            "transfer_proposals",
            "expedite_recommendations",
        ],
        "status": "scaffolded",
    }
