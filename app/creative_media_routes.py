from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from app.creative_media import creative_media_store

router = APIRouter(tags=["creative-media"])


@router.get("/media/creative/{media_id}", response_class=Response)
def get_creative_media(media_id: UUID) -> Response:
    try:
        media = creative_media_store.get(media_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creative media not found") from exc

    return Response(
        content=media.content,
        media_type=media.mime_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Length": str(media.byte_size),
            "ETag": f'"{media.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
