import pytest
import os
import json
import requests
from unittest.mock import patch, MagicMock, mock_open
from core.publisher import (
    load_accounts, get_account_credentials, load_scheduled_posts,
    save_scheduled_posts, add_to_queue, get_queue, upload_to_temporary_host,
    publish_item_local, search_locations
)

@patch("os.path.exists", return_value=False)
def test_load_accounts_missing(mock_exists):
    accounts = load_accounts()
    assert accounts == {}

@patch("builtins.open", new_callable=mock_open, read_data='{"test": {"access_token": "token"}}')
@patch("os.path.exists", return_value=True)
def test_load_accounts(mock_exists, mock_file):
    accounts = load_accounts()
    assert "test" in accounts
    assert accounts["test"]["access_token"] == "token"

def test_get_account_credentials():
    with patch("core.publisher.load_accounts") as mock_load:
        mock_load.return_value = {"eco": {"id": 1}}
        assert get_account_credentials("eco") == {"id": 1}
        assert get_account_credentials("none") is None

@patch("os.path.exists", return_value=False)
def test_load_scheduled_posts_missing(mock_exists):
    posts = load_scheduled_posts()
    assert posts == []

@patch("builtins.open", new_callable=mock_open, read_data='[]')
@patch("os.path.exists", return_value=True)
def test_load_scheduled_posts(mock_exists, mock_file):
    posts = load_scheduled_posts()
    assert posts == []

@patch("builtins.open", new_callable=mock_open)
@patch("json.dump")
def test_save_scheduled_posts(mock_json, mock_file):
    save_scheduled_posts([{"id": 1}])
    mock_json.assert_called_once()

@patch("core.publisher.load_scheduled_posts", return_value=[])
@patch("core.publisher.save_scheduled_posts")
def test_add_to_queue(mock_save, mock_load):
    add_to_queue([{"cap": "test"}])
    mock_save.assert_called_once()
    saved_posts = mock_save.call_args[0][0]
    assert saved_posts[0]["status"] == "pending"

@patch("core.publisher.load_scheduled_posts", return_value=[{"status": "pending"}, {"status": "done"}])
def test_get_queue(mock_load):
    queue = get_queue()
    assert len(queue) == 1
    assert queue[0]["status"] == "pending"

@patch("requests.get")
@patch("requests.post")
def test_upload_to_temporary_host_gofile(mock_post, mock_get):
    # Mock Gofile success
    mock_get.return_value.json.return_value = {"status": "ok", "data": {"servers": [{"name": "srv1"}]}}
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"status": "ok", "data": {"downloadPage": "http://gofile.io/123"}}
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "http://gofile.io/123"

@patch("requests.get", side_effect=Exception("Fail"))
@patch("requests.post")
def test_upload_to_temporary_host_uguu(mock_post, mock_get):
    # Gofile fails, try Uguu
    mock_post.return_value.status_code = 200
    mock_post.return_value.text = "https://uguu.se/test.mp4"
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://uguu.se/test.mp4"

@patch("requests.get", side_effect=Exception("Fail"))
@patch("requests.post")
def test_upload_to_temporary_host_catbox(mock_post, mock_get):
    # Gofile and Uguu fail, try Catbox
    # First post (Uguu) fails
    mock_post.side_effect = [Exception("Uguu fail"), MagicMock(status_code=200, text="https://files.catbox.moe/test.mp4")]
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://files.catbox.moe/test.mp4"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value=None)
@patch("core.publisher.log_print")
def test_publish_item_local_upload_fail(mock_log, mock_upload, mock_post, mock_get_creds):
    post = {"video_path": "test.mp4", "caption": "test", "platforms": ["instagram_reel"]}
    publish_item_local(post)
    mock_log.assert_called_with("No temporary URL could be generated.", "ERROR")

@patch("requests.post")
def test_upload_to_ig_error_code_3(mock_post):
    from core.publisher import _upload_to_ig
    mock_post.return_value.json.return_value = {"error": {"code": 3}}
    with patch("time.sleep"):
        res = _upload_to_ig("http://url", "cap", "tk", "ig", "REELS")
        assert res["error"] == "LIVE_MODE_RESTRICTION"

@patch("requests.post")
def test_upload_to_ig_exception(mock_post):
    from core.publisher import _upload_to_ig
    mock_post.side_effect = Exception("API ERR")
    with patch("time.sleep"):
        res = _upload_to_ig("http://url", "cap", "tk", "ig", "REELS")
        assert res is None

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value="http://url")
@patch("time.sleep")
@patch("core.publisher.upload_short")
@patch("core.publisher.upload_facebook_video")
def test_publish_item_local_all_platforms(mock_fb, mock_yt, mock_sleep, mock_upload, mock_post, mock_get_creds):
    post = {
        "caption": "test",
        "platforms": ["instagram_reel", "instagram_story", "facebook_reel", "facebook_story", "youtube_shorts"],
        "video_path": "path/to/vid.mp4"
    }
    # Mock IG upload sequence
    mock_post.return_value.json.side_effect = [
        {"id": "c1"}, {"status_code": "FINISHED"}, {"id": "p1"}, # Reel
        {"id": "c2"}, {"status_code": "FINISHED"}, {"id": "p2"}  # Story
    ]
    publish_item_local(post)
    assert mock_yt.called
    assert mock_fb.call_count == 2

@patch("requests.post")
def test_upload_facebook_video_pull(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="p_tk"):
        with patch("os.path.exists", return_value=False): # Force pull phase
            mock_post.return_value.json.side_effect = [
                {"video_id": "vid123", "upload_url": "http://up"},
                {"success": True}
            ]
            mock_post.return_value.status_code = 200
            res = upload_facebook_video("http://external/vid.mp4", "cap", "u_tk", "p_id")
            assert res["id"] == "vid123"

@patch("requests.post")
def test_upload_facebook_video_exception(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="tk"):
        mock_post.side_effect = Exception("General Err")
        res = upload_facebook_video("url", "cap", "tk", "id")
        assert res["error"] == "General Err"

@patch("core.publisher.get_account_credentials", return_value=None)
@patch("core.publisher.get_env_or_raise", return_value="env_tk")
@patch("requests.post")
def test_publish_item_local_env_fallback(mock_post, mock_env, mock_get_creds):
    post = {"platforms": ["instagram_reel"], "video_url": "http://url", "caption": "test"}
    mock_post.return_value.json.side_effect = [{"id": "c"}, {"status_code": "FINISHED"}, {"id": "p"}]
    with patch("time.sleep"):
        publish_item_local(post)
    assert mock_env.called

@patch("requests.get", side_effect=lambda x: MagicMock(json=lambda: {"status": "ok", "data": {"servers": [{"name": "s"}]}}) if "servers" in x else Exception("Upload failed"))
@patch("requests.post", side_effect=Exception("Post failed"))
def test_upload_to_temporary_host_retry_logic(mock_post, mock_get):
    # This will trigger the Uguu and Catbox fallbacks
    with patch("builtins.open", mock_open(read_data=b"data")):
        with patch("time.sleep"):
            url = upload_to_temporary_host("test.mp4")
            assert url is None # All fail but lines are covered

def test_get_page_access_token():
    from core.publisher import get_page_access_token
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"access_token": "page_tk"}
        assert get_page_access_token("u_tk", "p_id") == "page_tk"
        
        mock_get.side_effect = Exception("Err")
        assert get_page_access_token("u_tk", "p_id") is None

@patch("requests.post")
def test_upload_facebook_video_local(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="p_tk"):
        with patch("os.path.exists", return_value=True):
            with patch("os.path.getsize", return_value=100):
                with patch("builtins.open", mock_open(read_data=b"data")):
                    mock_post.return_value.json.side_effect = [
                        {"video_id": "vid123", "upload_url": "http://up"},
                        {"success": True}
                    ]
                    mock_post.return_value.status_code = 200
                    res = upload_facebook_video("local.mp4", "cap", "u_tk", "p_id")
                    assert res["id"] == "vid123"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk"})
@patch("requests.get", side_effect=Exception("API Error"))
def test_search_locations_error(mock_get, mock_creds):
    results = search_locations("Madrid")
    assert results == []
