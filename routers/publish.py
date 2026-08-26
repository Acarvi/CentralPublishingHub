from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends, Header
import hmac
import os
import tempfile
from pydantic import BaseModel
from typing import List, Optional
from core import publisher, youtube_uploader

def require_hub_api_key(x_api_key: Optional[str] = Header(default=None)):
    expected = os.getenv("HUB_API_KEY", "").strip()
    if expected and not hmac.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=403, detail="Invalid Hub API key")


router = APIRouter(dependencies=[Depends(require_hub_api_key)])

class PostPayload(BaseModel):
    video_url: Optional[str] = None
    video_path: Optional[str] = None
    caption: str
    target_time: Optional[str] = None
    platforms: List[str]
    location_id: Optional[str] = None
    shorts_title: Optional[str] = "Noticia"
    account_id: Optional[str] = "economika"
    source_app: Optional[str] = "EconomikaNoticias"
    platform_videos: Optional[dict[str, str]] = None
    original_media_type: Optional[str] = "video"
    variant: Optional[str] = "reel"
    scheduled_id: Optional[str] = None
    source_item_id: Optional[str] = None
    client_job_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    source_url: Optional[str] = None
    source_account: Optional[str] = None
    publish_mode: Optional[str] = None
    scheduled_at: Optional[str] = None
    expires_at: Optional[str] = None
    latest_publish_at: Optional[str] = None

class SchedulePayload(BaseModel):
    posts: List[PostPayload]

@router.post("/publish")
def publish_now(payload: PostPayload, background_tasks: BackgroundTasks):
    """
    Publish immediately to the specified platforms.
    """
    # Use a background task so we don't block the caller while uploading to multiple platforms
    background_tasks.add_task(
        publisher.publish_item_local, 
        payload.model_dump()
    )
    return {"status": "accepted", "message": "Publishing task started."}

@router.post("/schedule")
def schedule_batch(payload: SchedulePayload, background_tasks: BackgroundTasks):
    """
    Simulates the queuing of posts.
    """
    # For a robust system, this should write to a DB or a Queue (Redis, etc.)
    # For now, we will add them to the local scheduled JSON or memory        # Call the publisher to queue the items
    try:
        raw_posts = [p.model_dump() for p in payload.posts]
        scheduled = publisher.add_to_queue(raw_posts)
        return {
            "status": "success",
            "schedule_status": "scheduled",
            "message": f"{len(scheduled)} posts scheduled.",
            "posts": [
                {
                    "scheduled_id": post.get("scheduled_id"),
                    "source_item_id": post.get("source_item_id"),
                    "client_job_id": post.get("client_job_id"),
                    "idempotency_key": post.get("idempotency_key"),
                    "target_time": post.get("target_time"),
                    "platforms": post.get("platforms"),
                    "status": post.get("status", "pending"),
                }
                for post in scheduled
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/publish-now")
async def publish_now_api(payload: PostPayload):
    """
    Endpoint to publish an item immediately across platforms with persistent idempotency.
    """
    try:
        # Call the publisher with persistent idempotency to deduplicate replays
        result = publisher.publish_now_with_idempotency(payload.model_dump())
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queue")
def get_queue(all: bool = False, status: Optional[str] = None):
    if all or status:
        posts = publisher.load_scheduled_posts()
        if status:
            posts = [p for p in posts if p.get("status") == status]
        return {"pending": [p for p in posts if p.get("status") == "pending"], "posts": posts}
    pending = publisher.get_queue()
    return {"pending": pending, "posts": pending}


@router.post("/media/upload")
async def upload_scheduled_media(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    handle = tempfile.NamedTemporaryFile(prefix="hub_incoming_", suffix=suffix, delete=False)
    path = handle.name
    try:
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
        handle.close()
        video_url = publisher.upload_to_temporary_host(path)
        if not video_url:
            raise HTTPException(status_code=502, detail="No se pudo alojar el reel para programacion remota")
        return {"video_url": video_url}
    finally:
        try:
            handle.close()
        except Exception:
            pass
        try:
            os.remove(path)
        except OSError:
            pass

@router.get("/locations")
def search_locations(q: str, account_id: str = "economika"):
    results = publisher.search_locations(q, account_id)
    return {"results": results}
