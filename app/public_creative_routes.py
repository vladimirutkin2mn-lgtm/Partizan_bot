from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from app.creative_blob_store import creative_blob_store

router = APIRouter(tags=["public-creatives"])


@router.get("/public/creative-blobs/{blob_id}", response_class=Response)
async def get_public_creative_blob(blob_id: UUID) -> Response:
    try:
        view, data = creative_blob_store.get(blob_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creative blob not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Creative blob is unavailable") from exc
    return Response(
        content=data,
        media_type=view.mime_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{view.sha256}"',
            "Content-Length": str(view.byte_size),
        },
    )
