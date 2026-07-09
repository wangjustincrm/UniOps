"""Admin endpoints for MeetingRoom CRUD, status changes, and xlsx import."""
from __future__ import annotations

import io
import uuid
from datetime import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.core.deps import SessionDep
from app.core.permissions import AdminUser
from app.crud.room import (
    change_room_status,
    create_room,
    get_room,
    list_rooms,
    update_room,
)
from app.schemas.room import (
    RoomCreate,
    RoomOut,
    RoomUpdate,
    StatusChangeIn,
    StatusChangeOut,
)

router = APIRouter()


@router.post("", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
async def create_room_endpoint(
    data: RoomCreate,
    db: SessionDep,
    _: AdminUser,
) -> Any:
    try:
        room = await create_room(db, data)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room code already exists")
    return room


@router.get("", response_model=list[RoomOut])
async def list_rooms_endpoint(
    db: SessionDep,
    _: AdminUser,
    floor: str | None = None,
    area: str | None = None,
    status_filter: str | None = None,
) -> Any:
    return await list_rooms(db, floor=floor, area=area, status=status_filter)


# NOTE: /import must come before /{id} so FastAPI doesn't swallow "import" as a UUID
@router.post("/import")
async def import_rooms_xlsx(
    request: Request,
    db: SessionDep,
    _: AdminUser,
) -> dict:
    """Import rooms from an xlsx file (raw body or multipart).

    Accepts either:
    - Raw xlsx bytes with Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    - Multipart form with a single file field

    Returns: {created: n, errors: [{row: n, message: str}]}
    """
    import openpyxl
    from starlette.datastructures import UploadFile as StarletteUploadFile

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        # Take the first uploaded file field; skip plain text form fields.
        for _key, field in form.items():
            if not isinstance(field, StarletteUploadFile):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Expected a file upload field, got a plain text field: {_key!r}",
                )
            file_bytes = await field.read()
            break
        else:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No file uploaded")
    else:
        # Raw bytes upload
        file_bytes = await request.body()

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse xlsx: {exc}",
        )

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"created": 0, "errors": []}

    # First row is headers
    headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    created = 0
    errors: list[dict] = []

    for row_idx, row in enumerate(rows[1:], start=2):
        # rows[0] = header (excel row 1); rows[1] = first data row (excel row 2).
        # enumerate(rows[1:], start=2) → row_idx == excel row number directly.
        excel_row_number = row_idx

        row_data: dict[str, Any] = {}
        for col_idx, key in enumerate(headers):
            if col_idx < len(row):
                row_data[key] = row[col_idx]

        # Parse fields
        try:
            name = str(row_data.get("name") or "").strip()
            code = str(row_data.get("code") or "").strip()
            if not name:
                raise ValueError("name is required")
            if not code:
                raise ValueError("code is required")

            capacity_raw = row_data.get("capacity")
            try:
                capacity = int(capacity_raw) if capacity_raw not in (None, "") else 0
            except (TypeError, ValueError):
                capacity = 0
            if capacity <= 0:
                raise ValueError(f"capacity must be > 0, got {capacity_raw!r}")

            equipment_raw = str(row_data.get("equipment") or "").strip()
            equipment = [e.strip() for e in equipment_raw.split(",") if e.strip()] if equipment_raw else []

            def parse_time(val: Any) -> time | None:
                if val is None or str(val).strip() == "":
                    return None
                s = str(val).strip()
                # Handle HH:MM or HH:MM:SS
                parts = s.split(":")
                if len(parts) >= 2:
                    return time(int(parts[0]), int(parts[1]))
                return None

            room_data = RoomCreate(
                name=name,
                code=code,
                campus=str(row_data.get("campus") or "").strip() or None,
                building=str(row_data.get("building") or "").strip() or None,
                floor=str(row_data.get("floor") or "").strip() or None,
                area=str(row_data.get("area") or "").strip() or None,
                capacity=capacity,
                equipment=equipment,
                room_type=str(row_data.get("room_type") or "standard").strip() or "standard",
                open_time_start=parse_time(row_data.get("open_time_start")),
                open_time_end=parse_time(row_data.get("open_time_end")),
            )
        except ValueError as e:
            errors.append({"row": excel_row_number, "message": str(e)})
            continue

        try:
            await create_room(db, room_data)
            created += 1
        except IntegrityError:
            await db.rollback()
            errors.append({"row": excel_row_number, "message": f"Duplicate room code: {room_data.code}"})

    return {"created": created, "errors": errors}


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room_endpoint(
    room_id: uuid.UUID,
    data: RoomUpdate,
    db: SessionDep,
    _: AdminUser,
) -> Any:
    room = await get_room(db, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    try:
        room = await update_room(db, room, data)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room code already exists")
    return room


@router.post("/{room_id}/status", response_model=StatusChangeOut)
async def change_status_endpoint(
    room_id: uuid.UUID,
    data: StatusChangeIn,
    db: SessionDep,
    _: AdminUser,
) -> Any:
    room = await get_room(db, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    room, affected = await change_room_status(db, room, data.status, data.notes)
    return StatusChangeOut(room=RoomOut.model_validate(room), affected_future_bookings=affected)
