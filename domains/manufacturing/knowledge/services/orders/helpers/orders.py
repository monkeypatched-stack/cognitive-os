from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.orders.helpers import (
    order_customer_metrics as metrics_crud,
    order_details as details_crud,
    order_metadata as metadata_crud,
    order_payment_metadata as payment_crud,
)
from services.orders.models.order_customer_metrics import CustomerOrderMetricsCreate
from services.orders.models.order_metadata import OrderMetadataCreate
from services.orders.models.order_payment_metadata import OrderPaymentMetadataCreate
from services.orders.models.orders import OrderCreate, OrderResponse, OrderUpdate


async def _build_response(db: AsyncIOMotorDatabase, details: dict) -> OrderResponse:
    order_id = details["order_id"]
    return OrderResponse(
        order_id=order_id,
        details=details,
        metadata=await metadata_crud.get_by_order_id(db, order_id),
        payment=await payment_crud.get_by_order_id(db, order_id),
        metrics=await metrics_crud.get_by_order_id(db, order_id),
    )


def _with_order_id(payload, order_id: str, model_cls):
    if payload is None:
        return None
    data = payload.model_dump()
    data["order_id"] = order_id
    return model_cls(**data)


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[OrderResponse], int]:
    detail_records, total = await details_crud.get_all(
        db, page=page, page_size=page_size
    )
    return [await _build_response(db, details) for details in detail_records], total


async def get_by_order_id(
    db: AsyncIOMotorDatabase,
    order_id: str,
) -> Optional[OrderResponse]:
    details = await details_crud.get_by_order_id(db, order_id)
    if not details:
        return None
    return await _build_response(db, details)


async def create(db: AsyncIOMotorDatabase, data: OrderCreate) -> OrderResponse:
    order_id = data.details.order_id
    await details_crud.create(db, data.details)

    metadata = _with_order_id(data.metadata, order_id, OrderMetadataCreate)
    payment = _with_order_id(data.payment, order_id, OrderPaymentMetadataCreate)
    metrics = _with_order_id(data.metrics, order_id, CustomerOrderMetricsCreate)

    if metadata is not None:
        await metadata_crud.create(db, metadata)
    if payment is not None:
        await payment_crud.create(db, payment)
    if metrics is not None:
        await metrics_crud.create(db, metrics)

    created = await get_by_order_id(db, order_id)
    if created is None:
        raise RuntimeError(f"Order '{order_id}' was not created")
    return created


async def update(
    db: AsyncIOMotorDatabase,
    order_id: str,
    data: OrderUpdate,
) -> Optional[OrderResponse]:
    if not await details_crud.get_by_order_id(db, order_id):
        return None

    if data.details is not None:
        await details_crud.update(db, order_id, data.details)
    if data.metadata is not None:
        if await metadata_crud.get_by_order_id(db, order_id):
            await metadata_crud.update(db, order_id, data.metadata)
        else:
            await metadata_crud.create(
                db,
                OrderMetadataCreate(
                    order_id=order_id, **data.metadata.model_dump(exclude_unset=True)
                ),
            )
    if data.payment is not None:
        if await payment_crud.get_by_order_id(db, order_id):
            await payment_crud.update(db, order_id, data.payment)
        else:
            await payment_crud.create(
                db,
                OrderPaymentMetadataCreate(
                    order_id=order_id, **data.payment.model_dump(exclude_unset=True)
                ),
            )
    if data.metrics is not None:
        if await metrics_crud.get_by_order_id(db, order_id):
            await metrics_crud.update(db, order_id, data.metrics)
        else:
            await metrics_crud.create(
                db,
                CustomerOrderMetricsCreate(
                    order_id=order_id, **data.metrics.model_dump(exclude_unset=True)
                ),
            )

    return await get_by_order_id(db, order_id)


async def delete(db: AsyncIOMotorDatabase, order_id: str) -> bool:
    deleted = await details_crud.delete(db, order_id)
    await metadata_crud.delete(db, order_id)
    await payment_crud.delete(db, order_id)
    await metrics_crud.delete(db, order_id)
    return deleted
