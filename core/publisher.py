import os
import json
import time
import requests
import re
from datetime import datetime
from core.config import DATA_DIR, get_env_or_raise, get_env_optional
from core.logger import log_print, trigger_token_setup
from core.youtube_uploader import upload_short

SCHEDULED_POSTS_FILE = os.path.join(DATA_DIR, "scheduled_posts.json")
ACCOUNTS_DB_FILE = os.path.join(DATA_DIR, "accounts_db.json")

def load_accounts():
    if os.path.exists(ACCOUNTS_DB_FILE):
        with open(ACCOUNTS_DB_FILE, 'r') as f:
            return json.load(f)
    return {}

def get_account_credentials(account_id: str):
    accounts = load_accounts()
    return accounts.get(account_id)

def load_scheduled_posts():
    if os.path.exists(SCHEDULED_POSTS_FILE):
        with open(SCHEDULED_POSTS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_scheduled_posts(posts):
    with open(SCHEDULED_POSTS_FILE, 'w') as f:
        json.dump(posts, f, indent=2, default=str)

def add_to_queue(new_posts: list):
    posts = load_scheduled_posts()
    for p in new_posts:
        p['status'] = 'pending'
    posts.extend(new_posts)
    save_scheduled_posts(posts)

def get_queue():
    posts = load_scheduled_posts()
    return [p for p in posts if p.get('status') == 'pending']

def upload_to_temporary_host(file_path):
    """Uploads a file to get a public URL. Fallback: Gofile -> Uguu -> Catbox."""
    ext = os.path.splitext(file_path)[1].lower()
    m_filename = f"video_{int(time.time())}{ext}"

    # 1. Gofile.io (Fast and reliable)
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

    # 2. Uguu.se
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
        
    # 3. Catbox.moe
    log_print(f"Uploading {os.path.basename(file_path)} to catbox.moe...")
    url = "https://catbox.moe/user/api.php"
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(1, 3):
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
        except Exception as e:
            pass
        time.sleep(2)
        
    return None

def get_page_access_token(user_access_token, page_id):
    url = f"https://graph.facebook.com/v22.0/{page_id}?fields=access_token&access_token={user_access_token}"
    try:
        res = requests.get(url).json()
        return res.get("access_token")
    except Exception as e:
        return None

def _upload_to_ig(video_url, caption, access_token, ig_user_id, media_type, scheduled_publish_time=None, location_id=None):
    time.sleep(5)
    fb_page_id = get_env_optional("FB_PAGE_ID")
    page_access_token = get_page_access_token(access_token, fb_page_id) if fb_page_id else access_token
    effective_token = page_access_token if page_access_token else access_token

    url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media"
    payload = {
        "media_type": media_type,
        "video_url": video_url,
        "access_token": effective_token
    }
    if caption: payload["caption"] = caption
    if location_id: payload["location_id"] = location_id
    
    is_scheduled = scheduled_publish_time and media_type == "REELS"
    if is_scheduled:
        payload["scheduled_publish_time"] = int(scheduled_publish_time)

    try:
        response = requests.post(url, data=payload)
        result = response.json()
        
        if "id" in result:
            creation_id = result["id"]
            status_url = f"https://graph.facebook.com/v22.0/{creation_id}?fields=status_code,status&access_token={effective_token}"
            
            for _ in range(30):
                time.sleep(10)
                try:
                    status_res = requests.get(status_url).json()
                    status = status_res.get("status_code")
                    if status == "FINISHED":
                        if is_scheduled:
                            log_print(f"[SUCCESS] IG {media_type} scheduled. ID: {creation_id}")
                            return {"id": creation_id}
                        else:
                            publish_url = f"https://graph.facebook.com/v22.0/{ig_user_id}/media_publish"
                            publish_payload = {"creation_id": creation_id, "access_token": effective_token}
                            publish_res = requests.post(publish_url, data=publish_payload).json()
                            log_print(f"[SUCCESS] IG {media_type} published!")
                            return publish_res
                    elif status == "ERROR":
                        log_print(f"[ERROR] IG {media_type} container failed: {status_res}", "ERROR")
                        return None
                except Exception:
                    continue
        else:
            log_print(f"[ERROR] IG container error: {result}", "ERROR")
            if result.get("error", {}).get("code") == 3:
                return {"error": "LIVE_MODE_RESTRICTION"}
    except Exception as e:
        log_print(f"[ERROR] Exception in IG upload: {e}", "ERROR")
    return None

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
        start_res = requests.post(start_url, data=start_payload).json()
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
            requests.post(pull_url, data=pull_payload)
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
            
        final_res = requests.post(finish_url, data=finish_payload).json()
        return {"id": video_id} if "success" in final_res or "id" in final_res else final_res
            
    except Exception as e:
        return {"error": str(e)}

def publish_item_local(post: dict):
    """Executes the complete publishing flow for a single item"""
    account_id = post.get('account_id', 'economika')
    creds = get_account_credentials(account_id)
    
    if not creds:
        # Fallback to env vars if not in DB
        access_token = get_env_or_raise("META_ACCESS_TOKEN")
        ig_user_id = get_env_or_raise("IG_USER_ID")
        fb_page_id = get_env_or_raise("FB_PAGE_ID")
    else:
        access_token = creds.get("instagram_access_token")
        ig_user_id = creds.get("instagram_user_id")
        fb_page_id = creds.get("facebook_page_id")
    
    platforms = post.get('platforms', [])
    video_path = post.get('video_path')
    video_url = post.get('video_url')
    
    if video_path and not video_url:
        video_url = upload_to_temporary_host(video_path)
        if not video_url:
            log_print("No temporary URL could be generated.", "ERROR")
            return
            
    if 'instagram_reel' in platforms:
        _upload_to_ig(video_url, post['caption'], access_token, ig_user_id, "REELS", location_id=post.get('location_id'))
    if 'instagram_story' in platforms:
        _upload_to_ig(video_url, None, access_token, ig_user_id, "STORIES", location_id=post.get('location_id'))
    if 'facebook_reel' in platforms:
        upload_facebook_video(video_path or video_url, post['caption'], access_token, fb_page_id, is_story=False)
    if 'facebook_story' in platforms:
        upload_facebook_video(video_path or video_url, None, access_token, fb_page_id, is_story=True)
    if 'youtube_shorts' in platforms and video_path:
        upload_short(video_path, post.get('shorts_title', 'Noticia'), post['caption'])
