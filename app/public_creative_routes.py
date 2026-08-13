from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from app.creative_blob_store import creative_blob_store
from app.creative_video_blob_store import creative_video_blob_store

router = APIRouter(tags=["public-creatives"])


def _blob_response(*, data: bytes, mime_type: str, sha256: str, byte_size: int) -> Response:
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{sha256}"',
            "Content-Length": str(byte_size),
        },
    )


@router.get("/public/creative-blobs/{blob_id}", response_class=Response)
async def get_public_creative_blob(blob_id: UUID) -> Response:
    try:
        view, data = creative_blob_store.get(blob_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creative blob not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Creative blob is unavailable") from exc
    return _blob_response(
        data=data,
        mime_type=view.mime_type,
        sha256=view.sha256,
        byte_size=view.byte_size,
    )


@router.get("/public/creative-video-blobs/{blob_id}", response_class=Response)
async def get_public_creative_video_blob(blob_id: UUID) -> Response:
    try:
        view, data = creative_video_blob_store.get(blob_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creative video blob not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Creative video blob is unavailable") from exc
    return _blob_response(
        data=data,
        mime_type=view.mime_type,
        sha256=view.sha256,
        byte_size=view.byte_size,
    )
