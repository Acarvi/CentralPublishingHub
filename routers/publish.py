from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from core import publisher, youtube_uploader

router = APIRouter()

class PostPayload(BaseModel):
    video_url: Optional[str] = None
    video_path: Optional[str] = None
    caption: str
    target_time: Optional[str] = None
    platforms: List[str]
    location_id: Optional[str] = None
    shorts_title: Optional[str] = "Noticia"
    account_id: Optional[str] = "economika"

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
        payload.dict()
    )
    return {"status": "accepted", "message": "Publishing task started."}

@router.post("/schedule")
def schedule_batch(payload: SchedulePayload, background_tasks: BackgroundTasks):
    """
    Simulates the queuing of posts.
    """
    # For a robust system, this should write to a DB or a Queue (Redis, etc.)
    # For now, we will add them to the local scheduled JSON or memory if we retain the Economika pattern.
    publisher.add_to_queue([p.dict() for p in payload.posts])
    return {"status": "ok", "queued": len(payload.posts)}

@router.get("/queue")
def get_queue():
    pending = publisher.get_queue()
    return {"pending": pending}

@router.get("/locations")
def search_locations(q: str, account_id: str = "economika"):
    results = publisher.search_locations(q, account_id)
    return {"results": results}
