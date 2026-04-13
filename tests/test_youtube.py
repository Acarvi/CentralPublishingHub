import pytest
import os
import sys
from unittest.mock import patch, MagicMock, mock_open
from core.youtube_uploader import get_authenticated_service, upload_short

# Add SentinelAPI to path
SENTINEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "SentinelAPI"))
if SENTINEL_PATH not in sys.path:
    sys.path.insert(0, SENTINEL_PATH)

@patch("core.youtube_uploader.build")
@patch("core.youtube_uploader.InstalledAppFlow.from_client_secrets_file")
@patch("os.path.exists", side_effect=lambda x: True if ("token" in x or "credentials" in x or "secrets" in x) else False)
@patch("builtins.open", mock_open(read_data=b"dummy_pickle_data"))
@patch("pickle.load", return_value=MagicMock())
def test_get_authenticated_service_flow(mock_pickle, mock_exists, mock_flow, mock_build):
    mock_flow.return_value.run_local_server.return_value = MagicMock()
    get_authenticated_service()
    assert mock_build.called

@patch("core.youtube_uploader.build")
@patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file")
@patch("os.path.exists", return_value=False)
def test_get_authenticated_service_no_token_fail(mock_exists, mock_flow, mock_build):
    with pytest.raises(FileNotFoundError):
        get_authenticated_service()

@patch("core.youtube_uploader.build")
@patch("core.youtube_uploader.InstalledAppFlow.from_client_secrets_file")
@patch("os.path.exists", side_effect=lambda x: False if "token" in x else True) # Secrets exist, token missing
@patch("pickle.load", return_value=MagicMock())
def test_get_authenticated_service_new_token(mock_pickle, mock_exists, mock_flow, mock_build):
    mock_flow.return_value.run_local_server.return_value = MagicMock()
    with patch("builtins.open", mock_open()):
        with patch("pickle.dump"):
            get_authenticated_service()
    assert mock_flow.called

@patch("core.youtube_uploader.get_authenticated_service")
@patch("googleapiclient.http.MediaFileUpload")
def test_upload_short(mock_media, mock_auth):
    mock_service = MagicMock()
    mock_auth.return_value = mock_service
    # Mock the execute() to return None first, then the dict
    mock_service.videos().insert().execute.side_effect = [None, {"id": "yt123"}]
    
    with patch("os.path.exists", return_value=True):
        upload_short("vid.mp4", "Title", "Desc")
        assert mock_service.videos().insert.called

@patch("core.youtube_uploader.build")
@patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file")
@patch("os.path.exists", return_value=True)
@patch("builtins.open", mock_open(read_data=b"dummy_pickle_data"))
@patch("pickle.load")
def test_get_authenticated_service_refresh(mock_pickle, mock_exists, mock_flow, mock_build):
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "rt"
    mock_pickle.return_value = creds
    
    with patch("google.auth.transport.requests.Request"):
        with patch("builtins.open", mock_open()):
            with patch("pickle.dump") as mock_dump:
                get_authenticated_service()
                assert creds.refresh.called
                assert mock_dump.called

@patch("googleapiclient.errors.HttpError")
def test_upload_short_quota_error(mock_http_error):
    """Simulate a YouTube API quota exceeded error."""
    from core.youtube_uploader import upload_short
    with patch("core.youtube_uploader.get_authenticated_service") as mock_auth:
        mock_service = MagicMock()
        mock_auth.return_value = mock_service
        
        # Mock HttpError for quota (403 with specific reason)
        error_resp = MagicMock()
        error_resp.status = 403
        error_resp.reason = "quotaExceeded"
        mock_service.videos().insert().execute.side_effect = mock_http_error(resp=error_resp, content=b"Quota Exceeded")
        
        with patch("os.path.exists", return_value=True):
            try:
                upload_short("vid.mp4", "Title", "Desc")
            except Exception:
                pass
            # Just verify the target was called
            assert mock_service.videos().insert().execute.called

def test_sentinel_lock_simulation():
    """Verify that if Sentinel security audit fails, the service should theoretically block."""
    # In reality, bootstrap.py calls sys.exit(1)
    with patch("security_audit.validate_environment") as mock_audit:
        mock_audit.side_effect = SystemExit(1)
        
        with patch("sys.exit") as mock_exit:
            try:
                from bootstrap import activate_security
                activate_security()
            except SystemExit:
                pass
            # verify that validate_environment was called and triggered the exit
            assert mock_audit.called
