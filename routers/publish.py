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
    # For now, we will add them to the local scheduled JSON or memory        # Call the publisher to queue the items
    try:
        publisher.add_to_queue([p.model_dump() for p in payload.posts])
        return {"status": "success", "message": f"{len(payload.posts)} posts scheduled."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/publish-now")
async def publish_now_api(payload: PostPayload):
    """
    Endpoint to publish an item immediately across platforms.
    """
    try:
        # Call the publisher's local publish function
        result = publisher.publish_item_local(payload.model_dump())
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queue")
def get_queue():
    pending = publisher.get_queue()
    return {"pending": pending}

@router.get("/locations")
def search_locations(q: str, account_id: str = "economika"):
    results = publisher.search_locations(q, account_id)
    return {"results": results}
