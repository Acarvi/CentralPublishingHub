import os
import pickle
import json
import time
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.auth.exceptions import RefreshError
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

def get_authenticated_service():
    if not HAS_YOUTUBE_LIBS:
        raise ImportError("YouTube dependencies not installed.")
    
    credentials = None
    if os.path.exists(CREDENTIALS_FILE):
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

def upload_short(video_path: str, title: str = "Economika Noticias", description: str = "", publish_at: str = None) -> str:
    try:
        log_print(f"Starting upload for: {video_path}")
        youtube = get_authenticated_service()
        safe_title = title[:100]
        
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

        if "#shorts" not in description.lower() and "#shorts" not in safe_title.lower():
            description += "\n\n#Shorts"

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
        log_print(f"YouTube Short uploaded: https://youtube.com/shorts/{video_id}", "SUCCESS")
        return video_id
        
    except Exception as e:
        error_msg = str(e)
        if "uploadLimitExceeded" in error_msg or "quotaExceeded" in error_msg:
            return {"error": "quota_limit", "message": error_msg}
        elif "invalid_grant" in error_msg:
            trigger_token_setup('youtube')
            return {"error": "invalid_grant"}
        else:
            return {"error": "failed", "message": error_msg}
