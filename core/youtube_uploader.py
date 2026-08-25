import os
import pickle
import json
import time
import unicodedata
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    HAS_YOUTUBE_LIBS = True
except ImportError:
    HAS_YOUTUBE_LIBS = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.logger import log_print, trigger_token_setup
from core.config import CONFIG_DIR

SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.force-ssl']
CLIENT_SECRETS_FILE = os.path.join(CONFIG_DIR, 'client_secrets.json')
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, 'youtube_token.pickle')
EXPECTED_CHANNEL_NAME = os.getenv("YOUTUBE_EXPECTED_CHANNEL_NAME", "Economika Noticias")

def get_authenticated_service():
    if not HAS_YOUTUBE_LIBS:
        raise ImportError("YouTube dependencies not installed.")
    
    credentials = None
    token_json = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if token_json:
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    if os.path.exists(CREDENTIALS_FILE):
        if credentials is None:
            with open(CREDENTIALS_FILE, 'rb') as f:
                credentials = pickle.load(f)
    
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            log_print("Refreshing access token...", "INFO")
            try:
                credentials.refresh(Request())
                time.sleep(2)
            except RefreshError:
                os.remove(CREDENTIALS_FILE)
                trigger_token_setup('youtube')
                credentials = None
                
        if not credentials:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(f"Client secrets file not found at {CLIENT_SECRETS_FILE}")
                
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)
        
        with open(CREDENTIALS_FILE, 'wb') as f:
            pickle.dump(credentials, f)
    
    return build('youtube', 'v3', credentials=credentials)


def get_authenticated_channel(youtube=None) -> dict:
    youtube = youtube or get_authenticated_service()
    response = youtube.channels().list(part="snippet", mine=True).execute()
    items = response.get("items") or []
    if not items:
        return {}
    channel = items[0]
    return {
        "id": channel.get("id") or "",
        "title": (channel.get("snippet") or {}).get("title") or "",
    }


def channel_matches_expected(channel: dict) -> bool:
    def normalized(value: str) -> str:
        folded = unicodedata.normalize("NFKD", value.strip().casefold())
        return "".join(char for char in folded if not unicodedata.combining(char))

    actual = normalized(str(channel.get("title") or ""))
    expected = normalized(EXPECTED_CHANNEL_NAME)
    return bool(actual and actual == expected)

def upload_short(video_path: str, title: str = "Economika Noticias", description: str = "", publish_at: str = None) -> str:
    try:
        log_print(f"Starting upload for: {video_path}")
        youtube = get_authenticated_service()
        channel = get_authenticated_channel(youtube)
        if not channel_matches_expected(channel):
            return {
                "error": "wrong_youtube_channel",
                "message": f"Canal autenticado: {channel.get('title') or 'desconocido'}; esperado: {EXPECTED_CHANNEL_NAME}",
            }
        safe_title = title[:100]
        if "#shorts" not in description.lower() and "#shorts" not in safe_title.lower():
            description += "\n\n#Shorts"
        
        body = {
            'snippet': {
                'title': safe_title,
                'description': description,
                'tags': ['shorts', 'news', 'economika', 'libertad', 'economia'],
                'categoryId': '25'
            },
            'status': {
                'privacyStatus': 'private' if publish_at else 'public',
                'selfDeclaredMadeForKids': False
            }
        }
        
        if publish_at:
            body['status']['publishAt'] = publish_at

        media = MediaFileUpload(video_path, mimetype='video/mp4', resumable=True)
        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
        
        video_id = response.get('id')
        if not video_id:
            return {"error": "failed", "message": "YouTube no devolvio video_id"}
        verification = youtube.videos().list(
            part="status,processingDetails,contentDetails,snippet",
            id=video_id,
        ).execute()
        uploaded = (verification.get("items") or [{}])[0]
        status = uploaded.get("status") or {}
        processing = uploaded.get("processingDetails") or {}
        content = uploaded.get("contentDetails") or {}
        result = {
            "id": video_id,
            "url": f"https://youtube.com/shorts/{video_id}",
            "channel_id": channel.get("id") or "",
            "channel_title": channel.get("title") or "",
            "privacy_status": status.get("privacyStatus") or "",
            "upload_status": status.get("uploadStatus") or "",
            "rejection_reason": status.get("rejectionReason") or "",
            "failure_reason": status.get("failureReason") or "",
            "processing_status": processing.get("processingStatus") or "",
            "processing_failure_reason": processing.get("processingFailureReason") or "",
            "duration": content.get("duration") or "",
        }
        log_print(f"YouTube Short uploaded: {result['url']} privacy={result['privacy_status']} processing={result['processing_status']}", "SUCCESS")
        if not publish_at and result["privacy_status"] != "public":
            result["error"] = "not_public"
            result["message"] = f"YouTube dejo el Short como {result['privacy_status'] or 'desconocido'}"
        return result
        
    except Exception as e:
        error_msg = str(e)
        if "uploadLimitExceeded" in error_msg or "quotaExceeded" in error_msg:
            return {"error": "quota_limit", "message": error_msg}
        elif "invalid_grant" in error_msg:
            trigger_token_setup('youtube')
            return {"error": "invalid_grant"}
        else:
            return {"error": "failed", "message": error_msg}


