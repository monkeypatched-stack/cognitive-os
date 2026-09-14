import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from ..core.security import require_auth_context
from ..helpers.cad_conversion import convert_cad_upload_to_svg, safe_filename_stem

# These endpoints accept an upload and run a CAD conversion — they were completely
# unauthenticated, so any caller could burn CPU/memory converting arbitrary files.
router = APIRouter(
    tags=["CAD Conversion"], dependencies=[Depends(require_auth_context)]
)

# Bound the upload: an unbounded UploadFile lets a caller exhaust memory/disk.
MAX_UPLOAD_BYTES = int(
    os.getenv("CAD_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
)  # 25 MiB


async def _convert_to_svg(file: UploadFile) -> Response:
    svg = await convert_cad_upload_to_svg(file)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename_stem(file.filename)}.svg"'
        },
    )


@router.post("/convert/dwg-to-svg")
@router.post("/convert/cad-to-svg")
@router.post("/cad/convert-to-svg")
@router.post("/api/v1/convert/dwg-to-svg")
@router.post("/api/v1/cad/convert-to-svg")
async def convert_dwg_or_cad_to_svg(file: UploadFile = File(...)) -> Response:
    size = getattr(file, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )
    return await _convert_to_svg(file)
