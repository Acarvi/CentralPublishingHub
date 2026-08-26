import os
import json
import time
import requests
import re
import threading
import tempfile
from typing import Any
from datetime import datetime, timezone
from core.config import DATA_DIR, get_env_or_raise, get_env_optional
from core.logger import log_print, trigger_token_setup
from core.youtube_uploader import upload_short

SCHEDULED_POSTS_FILE = os.path.join(DATA_DIR, "scheduled_posts.json")
ACCOUNTS_DB_FILE = os.path.join(DATA_DIR, "accounts_db.json")
INSTAGRAM_PUBLIC_URL_TARGETS = {
    "instagram_reel",
    "instagram_story",
    "instagram_feed",
    "instagram_post",
    "instagram_original",
}
PLATFORM_ALIASES = {
    "instagram": "instagram_reel",
    "reel": "instagram_reel",
    "story": "instagram_story",
    "feed": "instagram_feed",
    "post": "instagram_feed",
    "instagram_post": "instagram_feed",
}
_QUEUE_LOCK = threading.Lock()

def load_accounts():
    if os.path.exists(ACCOUNTS_DB_FILE):
        with open(ACCOUNTS_DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def get_account_credentials(account_id: str):
    accounts = load_accounts()
    return accounts.get(account_id)

def load_scheduled_posts():
    if os.path.exists(SCHEDULED_POSTS_FILE):
        # The queue contains captions, source text and emojis. Never use the
        # Windows code page here; it corrupts/blocks scheduling responses.
        with open(SCHEDULED_POSTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_scheduled_posts(posts):
    # Google Drive and antivirus scanners can briefly hold the JSON file.
    # Write a complete sibling file first, flush and fsync, close it completely,
    # then replace the queue atomically so the worker never leaves a truncated
    # or locked queue behind.
    os.makedirs(os.path.dirname(SCHEDULED_POSTS_FILE), exist_ok=True)
    temp_path = f"{SCHEDULED_POSTS_FILE}.tmp"
    last_error = None
    for attempt in range(5):
        try:
            with open(temp_path, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(posts, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
                try:
                    fd = f.fileno() if hasattr(f, "fileno") else None
                    if isinstance(fd, int):
                        os.fsync(fd)
                except (AttributeError, OSError, TypeError):
                    pass
            # File is completely closed before atomic replace
            os.replace(temp_path, SCHEDULED_POSTS_FILE)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Failed to save scheduled posts atomically")

def add_to_queue(new_posts: list) -> list[dict[str, Any]]:
    """
    Adds posts to the scheduled queue with atomic persistence and real idempotency.
    If a post with the same idempotency_key (or scheduled_id) already exists,
    it reuses the persisted post instead of creating a duplicate.
    """
    with _QUEUE_LOCK:
        posts = load_scheduled_posts()
        resolved_posts = []
        changed = False
        for p in new_posts:
            idempotency_key = str(p.get("idempotency_key") or "").strip()
            scheduled_id = str(p.get("scheduled_id") or "").strip()
            client_job_id = str(p.get("client_job_id") or "").strip()

            # Check for existing job by idempotency_key
            existing = None
            if idempotency_key:
                for ep in posts:
                    if str(ep.get("idempotency_key") or "").strip() == idempotency_key:
                        existing = ep
                        break

            # Secondary fallback: match by exact scheduled_id or client_job_id
            if not existing and (scheduled_id or client_job_id):
                for ep in posts:
                    if scheduled_id and str(ep.get("scheduled_id") or "").strip() == scheduled_id:
                        existing = ep
                        break
                    if client_job_id and str(ep.get("client_job_id") or "").strip() == client_job_id:
                        existing = ep
                        break

            if existing:
                # Idempotent replay: reuse the canonical persisted job
                resolved_posts.append(existing)
            else:
                p["status"] = p.get("status") or "pending"
                p["scheduled_id"] = p.get("scheduled_id") or f"scheduled-{time.time_ns()}"
                p["queued_at"] = p.get("queued_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
                posts.append(p)
                resolved_posts.append(p)
                changed = True

        if changed:
            save_scheduled_posts(posts)
        return resolved_posts


def get_queue():
    posts = load_scheduled_posts()
    return [p for p in posts if p.get('status') == 'pending']


def publish_now_with_idempotency(payload: dict[str, Any], wait_timeout_seconds: float = 60.0) -> dict[str, Any]:
    """
    Executes immediate publishing with thread-safe persistent idempotency.
    Protects against concurrent requests with the same idempotency_key:
    - First request creates an 'immediate_processing' reservation under _QUEUE_LOCK and becomes owner.
    - Owner releases _QUEUE_LOCK, executes publish_item_local(), and updates the record with the canonical result.
    - Concurrent requests with the same key wait for the owner to finish and return the canonical result without re-executing.
    """
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    scheduled_id = str(payload.get("scheduled_id") or "").strip()
    client_job_id = str(payload.get("client_job_id") or "").strip()

    is_owner = False

    if idempotency_key or scheduled_id or client_job_id:
        while True:
            with _QUEUE_LOCK:
                posts = load_scheduled_posts()
                existing = None
                if idempotency_key:
                    for p in posts:
                        if str(p.get("idempotency_key") or "").strip() == idempotency_key:
                            existing = p
                            break
                if not existing and (scheduled_id or client_job_id):
                    for p in posts:
                        if scheduled_id and str(p.get("scheduled_id") or "").strip() == scheduled_id:
                            existing = p
                            break
                        if client_job_id and str(p.get("client_job_id") or "").strip() == client_job_id:
                            existing = p
                            break

                if existing:
                    if existing.get("result") is not None:
                        # Idempotent replay: return canonical persisted execution result
                        return existing["result"]
                    if existing.get("status") == "immediate_processing":
                        # Another thread/request is actively executing this operation
                        pass
                    else:
                        return existing.get("result") or {"status": existing.get("status", "unknown")}
                else:
                    # Reserve operation under lock: this request becomes the exclusive owner
                    reservation = dict(payload)
                    reservation["status"] = "immediate_processing"
                    reservation["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if not reservation.get("scheduled_id"):
                        reservation["scheduled_id"] = f"imm-{time.time_ns()}"
                    posts.append(reservation)
                    save_scheduled_posts(posts)
                    is_owner = True
                    break

            if not is_owner:
                # Bounded polling loop for concurrent request waiting on owner completion
                deadline = time.time() + wait_timeout_seconds
                while time.time() < deadline:
                    time.sleep(0.05)
                    with _QUEUE_LOCK:
                        posts = load_scheduled_posts()
                        existing = None
                        if idempotency_key:
                            for p in posts:
                                if str(p.get("idempotency_key") or "").strip() == idempotency_key:
                                    existing = p
                                    break
                        if not existing and (scheduled_id or client_job_id):
                            for p in posts:
                                if scheduled_id and str(p.get("scheduled_id") or "").strip() == scheduled_id:
                                    existing = p
                                    break
                                if client_job_id and str(p.get("client_job_id") or "").strip() == client_job_id:
                                    existing = p
                                    break
                        if existing and existing.get("result") is not None:
                            return existing["result"]
                        if not existing or existing.get("status") != "immediate_processing":
                            break

                with _QUEUE_LOCK:
                    posts = load_scheduled_posts()
                    for p in posts:
                        if idempotency_key and str(p.get("idempotency_key") or "").strip() == idempotency_key:
                            if p.get("result") is not None:
                                return p["result"]
                            return {"status": p.get("status", "immediate_processing"), "message": "Operation in progress"}
                return {"status": "immediate_processing", "message": "Operation in progress"}

    # Owner execution: execute publication OUTSIDE lock
    try:
        result = publish_item_local(payload)
    except Exception as exc:
        result = {
            "status": "error",
            "error": "immediate_publish_exception",
            "details": str(exc),
        }

    # Persist final result under lock
    with _QUEUE_LOCK:
        posts = load_scheduled_posts()
        post_record = dict(payload)
        post_record["status"] = "published" if result.get("status") == "success" else "error"
        post_record["result"] = result
        post_record["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not post_record.get("scheduled_id"):
            post_record["scheduled_id"] = f"imm-{time.time_ns()}"

        idx = None
        if idempotency_key:
            for i, p in enumerate(posts):
                if str(p.get("idempotency_key") or "").strip() == idempotency_key:
                    idx = i
                    break
        if idx is not None:
            posts[idx] = post_record
        else:
            posts.append(post_record)

        save_scheduled_posts(posts)

    return result


def process_due_posts(now: datetime | None = None) -> int:
    """Publish due queue entries and support explicit expiry policies without silent overdue loss."""
    now = now or datetime.now(timezone.utc)
    due_indexes = []
    with _QUEUE_LOCK:
        posts = load_scheduled_posts()
        changed = False
        for index, post in enumerate(posts):
            if post.get('status') == 'processing':
                continue
            if post.get('status') != 'pending':
                continue
            raw_target = post.get('target_time') or post.get('scheduled_at')
            if not raw_target:
                continue
            try:
                target = datetime.fromisoformat(raw_target.replace('Z', '+00:00'))
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if target > now:
                continue
            raw_expiry = post.get('expires_at') or post.get('latest_publish_at')
            if raw_expiry:
                try:
                    expiry = datetime.fromisoformat(str(raw_expiry).replace('Z', '+00:00'))
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if now > expiry:
                        post['status'] = 'expired'
                        post['error'] = 'schedule_explicitly_expired'
                        changed = True
                        continue
                except ValueError:
                    pass
            post['status'] = 'processing'
            post['started_at'] = now.isoformat(timespec='seconds')
            due_indexes.append(index)
            changed = True
        if changed:
            save_scheduled_posts(posts)

    for index in due_indexes:
        with _QUEUE_LOCK:
            posts = load_scheduled_posts()
            post = dict(posts[index])
        try:
            result = publish_item_local(post)
        except Exception as exc:
            # Never leave a due item stuck in `processing` when one platform
            # raises unexpectedly; persist a visible failure instead.
            result = {
                "status": "error",
                "error": "worker_exception",
                "details": str(exc),
            }
        success = result.get('status') == 'success'
        with _QUEUE_LOCK:
            posts = load_scheduled_posts()
            posts[index]['status'] = 'published' if success else 'error'
            posts[index]['result'] = result
            posts[index]['finished_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            save_scheduled_posts(posts)
    return len(due_indexes)

def recover_interrupted_posts() -> int:
    """Mark jobs left in processing or immediate_processing by a previous process as unknown states requiring reconciliation."""
    with _QUEUE_LOCK:
        posts = load_scheduled_posts()
        recovered = 0
        for post in posts:
            st = str(post.get("status") or "").strip()
            if st == "processing":
                post["status"] = "unknown"
                post["error"] = "scheduled_publish_interrupted_requires_reconciliation"
                post["result"] = {
                    "status": "unknown",
                    "error": "scheduled_publish_interrupted_requires_reconciliation",
                    "requires_reconciliation": True,
                }
                post["requires_reconciliation"] = True
                post["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                recovered += 1
            elif st == "immediate_processing":
                post["status"] = "unknown"
                post["error"] = "immediate_publish_interrupted_requires_reconciliation"
                post["result"] = {
                    "status": "unknown",
                    "error": "immediate_publish_interrupted_requires_reconciliation",
                    "requires_reconciliation": True,
                }
                post["requires_reconciliation"] = True
                post["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                recovered += 1
        if recovered:
            save_scheduled_posts(posts)
        return recovered


def scheduler_loop(stop_event: threading.Event, poll_seconds: int = 15) -> None:
    while not stop_event.is_set():
        try:
            process_due_posts()
        except Exception as exc:
            log_print(f"Scheduled publisher error: {exc}", "ERROR")
        stop_event.wait(poll_seconds)

def normalize_platforms(platforms: list[str]) -> list[str]:
    return [PLATFORM_ALIASES.get(platform, platform) for platform in platforms]

def requires_public_url(platforms: list[str]) -> bool:
    normalized_platforms = normalize_platforms(platforms)
    return any(platform in INSTAGRAM_PUBLIC_URL_TARGETS for platform in normalized_platforms)

def resolve_public_media_url(post: dict) -> tuple[str | None, dict | None]:
    """
    Resolve media into a public URL for Meta/Instagram Graph container creation.

    Returns (video_url, error).
    """
    video_url = post.get("video_url")
    if video_url:
        return video_url, None

    platforms = normalize_platforms(post.get("platforms", []))
    if not requires_public_url(platforms):
        return None, None

    video_path = post.get("video_path")
    if not video_path:
        return None, {
            "error": "PUBLIC_URL_REQUIRED",
            "details": "Instagram targets require video_url or uploadable video_path",
        }

    uploaded_url = upload_to_temporary_host(video_path)
    if not uploaded_url:
        return None, {
            "error": "TEMPORARY_UPLOAD_FAILED",
            "details": "Temporary hosting did not return a public URL",
        }

    return uploaded_url, None

def upload_to_temporary_host(file_path):
    """Uploads a file to get a public direct URL. Fallback: Catbox -> Litterbox -> Uguu -> Gofile."""
    ext = os.path.splitext(file_path)[1].lower()
    m_filename = f"video_{int(time.time())}{ext}"

    # 1. Catbox.moe. Meta/Instagram needs a direct media URL, not a download page.
    log_print(f"Uploading {os.path.basename(file_path)} to catbox.moe...")
    url = "https://catbox.moe/user/api.php"
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(1, 4):
        try:
            with open(file_path, 'rb') as f:
                files = {'fileToUpload': (m_filename, f)}
                data = {'reqtype': 'fileupload'}
                response = requests.post(url, files=files, data=data, headers=headers, timeout=300)
                if response.status_code == 200:
                    direct_url = response.text.strip()
                    if direct_url.startswith("http"):
                        log_print(f"Direct Catbox URL: {direct_url}", "SUCCESS")
                        return direct_url
                log_print(f"Catbox attempt {attempt} returned HTTP {response.status_code}: {response.text[:160]}", "WARNING")
        except Exception as e:
            log_print(f"Catbox upload failed: {e}", "WARNING")
        time.sleep(attempt * 3)

    # 2. Litterbox, Catbox's temporary direct-file service.
    log_print("Attempting upload to litterbox.catbox.moe...", "INFO")
    try:
        litterbox_url = "https://litterbox.catbox.moe/resources/internals/api.php"
        with open(file_path, 'rb') as f:
            files = {'fileToUpload': (m_filename, f)}
            data = {'reqtype': 'fileupload', 'time': '72h'}
            res = requests.post(litterbox_url, files=files, data=data, headers=headers, timeout=300)
            direct_url = res.text.strip()
            if res.status_code == 200 and direct_url.startswith("http"):
                log_print(f"Direct Litterbox URL: {direct_url}", "SUCCESS")
                return direct_url
            log_print(f"Litterbox returned HTTP {res.status_code}: {res.text[:160]}", "WARNING")
    except Exception as e:
        log_print(f"Litterbox fallback failed: {e}", "WARNING")

    # 3. Uguu.se
    log_print("Attempting upload to uguu.se...", "INFO")
    try:
        uguu_url = "https://uguu.se/upload.php"
        with open(file_path, 'rb') as f:
            files = {'files[]': (m_filename, f)}
            res = requests.post(uguu_url, files=files, timeout=300)
            if res.status_code == 200:
                content = res.text.strip()
                match = re.search(r'https?://uguu\.se/[^\s\]]+', content)
                if match:
                    direct_url = match.group(0)
                    log_print(f"Direct Uguu URL: {direct_url}", "SUCCESS")
                    return direct_url
    except Exception as e:
        log_print(f"Uguu fallback failed: {e}", "WARNING")

    # 4. Gofile.io, last because it may return a download page instead of direct media.
    log_print("Attempting upload to Gofile.io...", "INFO")
    try:
        server_res = requests.get("https://api.gofile.io/servers").json()
        if server_res.get("status") == "ok":
            server = server_res["data"]["servers"][0]["name"]
            gofile_url = f"https://{server}.gofile.io/contents/uploadfile"
            with open(file_path, 'rb') as f:
                res = requests.post(gofile_url, files={'file': f}, timeout=300)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "ok":
                        direct_url = data["data"]["downloadPage"]
                        log_print(f"Gofile URL uploaded: {direct_url}", "SUCCESS")
                        return direct_url
    except Exception as e:
        log_print(f"Gofile upload failed: {e}", "WARNING")
        
    return None

def get_page_access_token(user_access_token, page_id):
    url = f"https://graph.facebook.com/v22.0/{page_id}?fields=access_token&access_token={user_access_token}"
    try:
        res = requests.get(url, timeout=30).json()
        return res.get("access_token")
    except Exception as e:
        return None

def _upload_to_ig(video_url, caption, access_token, ig_user_id, media_type, scheduled_publish_time=None, location_id=None, page_id=None):
    time.sleep(5)
    # Account credentials are the source of truth for local/queued jobs. The
    # old implementation only read FB_PAGE_ID from the process environment,
    # silently ignoring the configured account record.
    fb_page_id = page_id or get_env_optional("FB_PAGE_ID")
    page_access_token = get_page_access_token(access_token, fb_page_id) if fb_page_id else access_token
    effective_token = page_access_token if page_access_token else access_token

    url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media"
    payload = {
        "media_type": media_type,
        "image_url" if media_type == "IMAGE" else "video_url": video_url,
        "access_token": effective_token
    }
    if caption: payload["caption"] = caption
    if location_id: payload["location_id"] = location_id
    
    is_scheduled = scheduled_publish_time and media_type == "REELS"
    if is_scheduled:
        payload["scheduled_publish_time"] = int(scheduled_publish_time)

    try:
        response = requests.post(url, data=payload, timeout=60)
        result = response.json()
        
        if "id" in result:
            creation_id = result["id"]
            status_url = f"https://graph.facebook.com/v22.0/{creation_id}?fields=status_code,status&access_token={effective_token}"
            
            for _ in range(30):
                time.sleep(10)
                try:
                    status_res = requests.get(status_url, timeout=30).json()
                    status = status_res.get("status_code")
                    if status == "FINISHED":
                        if is_scheduled:
                            log_print(f"[SUCCESS] IG {media_type} scheduled. ID: {creation_id}")
                            return {"id": creation_id}
                        else:
                            publish_url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media_publish"
                            publish_payload = {"creation_id": creation_id, "access_token": effective_token}
                            publish_res = requests.post(publish_url, data=publish_payload, timeout=60).json()
                            published_id = publish_res.get("id")
                            if published_id:
                                detail_url = f"https://graph.facebook.com/v22.0/{published_id}?fields=id,permalink&access_token={effective_token}"
                                detail = requests.get(detail_url, timeout=30).json()
                                if detail.get("permalink"):
                                    publish_res["permalink"] = detail["permalink"]
                            log_print(f"[SUCCESS] IG {media_type} published! {publish_res.get('permalink', published_id or '')}")
                            return publish_res
                    elif status == "ERROR":
                        log_print(f"[ERROR] IG {media_type} container failed: {status_res}", "ERROR")
                        return {
                            "error": "IG_CONTAINER_FAILED",
                            "message": status_res.get("status") or "Instagram rejected the media container",
                            "details": status_res,
                        }
                except Exception:
                    continue
            return {
                "error": "IG_CONTAINER_TIMEOUT",
                "message": "Instagram did not finish processing the media container in time",
            }
        else:
            log_print(f"[ERROR] IG container error: {result}", "ERROR")
            if result.get("error", {}).get("code") == 3:
                return {"error": "LIVE_MODE_RESTRICTION"}
            return {
                "error": "IG_CONTAINER_CREATE_FAILED",
                "message": result.get("error", {}).get("message") or "Instagram rejected the media container",
                "details": result,
            }
    except Exception as e:
        log_print(f"[ERROR] Exception in IG upload: {e}", "ERROR")
        return {"error": "IG_REQUEST_FAILED", "message": str(e)}
    return {"error": "IG_UNKNOWN_FAILURE", "message": "Instagram publishing returned no result"}

def upload_facebook_video(video_path_or_url, caption, access_token, page_id, is_story=False):
    page_access_token = get_page_access_token(access_token, page_id)
    if not page_access_token: return {"error": "Page Access Token Missing"}
    
    is_local = os.path.exists(video_path_or_url)
    endpoint = "video_stories" if is_story else "video_reels"
    
    start_url = f"https://graph.facebook.com/v22.0/{page_id}/{endpoint}"
    start_payload = {
        "upload_phase": "start",
        "access_token": page_access_token
    }
    
    try:
        start_res = requests.post(start_url, data=start_payload, timeout=60).json()
        video_id = start_res.get("video_id")
        upload_url = start_res.get("upload_url")
        
        if not video_id: return start_res
            
        if is_local:
            file_size = os.path.getsize(video_path_or_url)
            headers = {"Authorization": f"OAuth {page_access_token}", "offset": "0", "file_size": str(file_size)}
            with open(video_path_or_url, 'rb') as f:
                upload_res = requests.post(upload_url, headers=headers, data=f, timeout=600)
            if upload_res.status_code != 200: return {"error": "Upload phase failed"}
        else:
            pull_url = f"https://graph.facebook.com/v22.0/{page_id}/{endpoint}"
            pull_payload = {
                "upload_phase": "pull",
                "video_id": video_id,
                "file_url": video_path_or_url,
                "access_token": page_access_token
            }
            requests.post(pull_url, data=pull_payload, timeout=60)
            time.sleep(5) 
            
        finish_url = f"https://graph.facebook.com/v22.0/{page_id}/{endpoint}"
        finish_payload = {
            "upload_phase": "finish",
            "video_id": video_id,
            "access_token": page_access_token
        }
        if not is_story:
            finish_payload["description"] = caption
            finish_payload["video_state"] = "PUBLISHED"
            
        final_res = requests.post(finish_url, data=finish_payload, timeout=60).json()
        return {"id": video_id} if "success" in final_res or "id" in final_res else final_res
            
    except Exception as e:
        return {"error": str(e)}

def publish_item_local(post: dict):
    """Executes the complete publishing flow for a single item"""
    platforms = normalize_platforms(post.get('platforms', []))
    post["platforms"] = platforms
    platform_videos = post.get("platform_videos") or {}
    video_path = post.get('video_path')
    video_url, media_error = resolve_public_media_url(post)
    if media_error:
        log_print(media_error["details"], "ERROR")
        return {
            "status": "error",
            "error": media_error["error"],
            "details": media_error["details"],
        }

    account_id = post.get('account_id', 'economika')
    creds = get_account_credentials(account_id)
    
    if not creds:
        # Fallback to env vars if not in DB
        access_token = get_env_or_raise("META_ACCESS_TOKEN")
        ig_user_id = get_env_or_raise("IG_USER_ID")
        fb_page_id = get_env_or_raise("FB_PAGE_ID")
    else:
        access_token = creds.get("access_token")
        ig_user_id = creds.get("ig_user_id")
        fb_page_id = creds.get("fb_page_id")
    
    results = []

    if 'instagram_reel' in platforms:
        instagram_path = platform_videos.get("instagram") or video_path
        # The initial resolution above already uploaded the main video when
        # this is the same file. Reusing that URL avoids duplicate Catbox
        # uploads and prevents Meta from receiving a stale/duplicate asset.
        instagram_url_hint = video_url if instagram_path == video_path else None
        instagram_post = {**post, "video_path": instagram_path, "video_url": instagram_url_hint}
        instagram_url, instagram_error = resolve_public_media_url(instagram_post)
        try:
            result = instagram_error or _upload_to_ig(instagram_url, post['caption'], access_token, ig_user_id, "REELS", location_id=post.get('location_id'), page_id=fb_page_id)
        except Exception as exc:
            result = {"error": "instagram_exception", "message": str(exc)}
        results.append({"platform": "instagram_reel", "success": is_platform_success(result), "result": result})
    if 'instagram_original' in platforms:
        original_media_type = str(post.get("original_media_type") or "video").lower()
        # Meta has retired the VIDEO container. Raw videos must be sent as REELS, while images remain feed posts.
        original_media_kind = "IMAGE" if original_media_type == "image" else "REELS"
        try:
            result = _upload_to_ig(
                video_url,
                post['caption'],
                access_token,
                ig_user_id,
                original_media_kind,
                location_id=post.get('location_id'),
                page_id=fb_page_id,
            )
        except Exception as exc:
            result = {"error": "instagram_original_exception", "message": str(exc)}
        results.append({"platform": "instagram_original", "success": is_platform_success(result), "result": result})
    if 'instagram_story' in platforms:
        try:
            result = _upload_to_ig(video_url, None, access_token, ig_user_id, "STORIES", location_id=post.get('location_id'), page_id=fb_page_id)
        except Exception as exc:
            result = {"error": "instagram_story_exception", "message": str(exc)}
        results.append({"platform": "instagram_story", "success": is_platform_success(result), "result": result})
    if 'instagram_feed' in platforms:
        results.append({
            "platform": "instagram_feed",
            "success": False,
            "error": "NOT_IMPLEMENTED",
            "details": "Instagram Feed/Post publishing is a planned target and is not implemented yet.",
        })
    if 'facebook_reel' in platforms:
        try:
            result = upload_facebook_video(video_path or video_url, post['caption'], access_token, fb_page_id, is_story=False)
        except Exception as exc:
            result = {"error": "facebook_reel_exception", "message": str(exc)}
        results.append({"platform": "facebook_reel", "success": is_platform_success(result), "result": result})
    if 'facebook_story' in platforms:
        try:
            result = upload_facebook_video(video_path or video_url, None, access_token, fb_page_id, is_story=True)
        except Exception as exc:
            result = {"error": "facebook_story_exception", "message": str(exc)}
        results.append({"platform": "facebook_story", "success": is_platform_success(result), "result": result})
    if 'youtube_shorts' in platforms:
        downloaded_path = None
        youtube_path = platform_videos.get("youtube") or video_path
        try:
            # Remote scheduling stores public URLs in platform_videos. The
            # YouTube API needs a local file, so download the URL first.
            if youtube_path and str(youtube_path).startswith(("http://", "https://")):
                downloaded_path = _download_public_video(str(youtube_path))
                youtube_path = downloaded_path
            if not youtube_path and video_url:
                downloaded_path = _download_public_video(video_url)
                youtube_path = downloaded_path
            if not youtube_path:
                result = {"error": "missing_video", "message": "YouTube necesita video_path o video_url"}
            else:
                result = upload_short(youtube_path, post.get('shorts_title', 'Noticia'), post['caption'])
            results.append({"platform": "youtube_shorts", "success": is_platform_success(result), "result": result})
        except Exception as exc:
            results.append({
                "platform": "youtube_shorts",
                "success": False,
                "result": {"error": "youtube_exception", "message": str(exc)},
            })
        finally:
            if downloaded_path:
                try:
                    os.remove(downloaded_path)
                except OSError:
                    pass

    all_ok = bool(results) and all(item.get("success") for item in results)
    any_ok = any(item.get("success") for item in results)
    status = "success" if all_ok else ("partial_failed" if any_ok else "error")
    return {"status": status, "results": results}


def is_platform_success(result: Any) -> bool:
    """
    Explicit contract to determine real publishing success for a platform.
    Never treats error dictionaries, HTTP failures, exceptions, empty values or truthy error containers as success.
    """
    if not result or not isinstance(result, dict):
        return False
    if result.get("error") or result.get("errors") or result.get("error_message"):
        return False
    status_code = result.get("status_code")
    if isinstance(status_code, int) and status_code >= 400:
        return False
    if result.get("success") is False:
        return False
    if result.get("id") or result.get("video_id") or result.get("success") is True:
        return True
    return False


def _publish_succeeded(result: Any) -> bool:
    """Legacy alias preserved for internal callers."""
    return is_platform_success(result)


def _download_public_video(url: str) -> str:
    suffix = os.path.splitext(url.split("?", 1)[0])[1] or ".mp4"
    handle = tempfile.NamedTemporaryFile(prefix="hub_video_", suffix=suffix, delete=False)
    path = handle.name
    handle.close()
    try:
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with open(path, "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise

def search_locations(query: str, account_id: str = "economika"):
    creds = get_account_credentials(account_id)
    token = creds.get("access_token") if creds else get_env_or_raise("META_ACCESS_TOKEN")
    
    url = f"https://graph.facebook.com/v22.0/pages/search?q={query}&type=adlocation&access_token={token}"
    try:
        res = requests.get(url, timeout=30).json()
        return res.get("data", [])
    except Exception as e:
        log_print(f"Error searching locations: {e}", "ERROR")
        return []
