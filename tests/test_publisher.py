import pytest
import os
import json
import requests
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, mock_open
from core.publisher import (
    load_accounts, get_account_credentials, load_scheduled_posts,
    save_scheduled_posts, add_to_queue, get_queue, upload_to_temporary_host,
    publish_item_local, search_locations, requires_public_url,
    resolve_public_media_url, normalize_platforms, process_due_posts
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

@patch("os.replace")
@patch("builtins.open", new_callable=mock_open)
@patch("json.dump")
def test_save_scheduled_posts(mock_json, mock_file, mock_replace):
    save_scheduled_posts([{"id": 1}])
    mock_json.assert_called_once()
    mock_replace.assert_called_once()

@patch("core.publisher.load_scheduled_posts", return_value=[])
@patch("core.publisher.save_scheduled_posts")
def test_add_to_queue(mock_save, mock_load):
    res = add_to_queue([{"cap": "test"}])
    mock_save.assert_called_once()
    saved_posts = mock_save.call_args[0][0]
    assert saved_posts[0]["status"] == "pending"
    assert len(res) == 1


def test_add_to_queue_real_idempotency_avoids_duplicate_jobs(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import add_to_queue, load_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        # First request
        first_post = {
            "caption": "Noticia A",
            "idempotency_key": "idem-key-abc-123",
            "scheduled_id": "sch-post-1",
            "client_job_id": "attempt-1",
            "platforms": ["instagram_reel"],
        }
        res1 = add_to_queue([first_post])
        assert len(res1) == 1
        assert res1[0]["scheduled_id"] == "sch-post-1"
        assert res1[0]["client_job_id"] == "attempt-1"

        queue_after_first = load_scheduled_posts()
        assert len(queue_after_first) == 1

        # Second request (retransmission of same logical operation with different attempt ID)
        second_post = {
            "caption": "Noticia A",
            "idempotency_key": "idem-key-abc-123",
            "scheduled_id": "sch-post-2-retry",
            "client_job_id": "attempt-2-retry",
            "platforms": ["instagram_reel"],
        }
        res2 = add_to_queue([second_post])
        assert len(res2) == 1
        # Reuses canonical job persisted in queue
        assert res2[0]["scheduled_id"] == "sch-post-1"
        assert res2[0]["client_job_id"] == "attempt-1"
        assert res2[0]["idempotency_key"] == "idem-key-abc-123"

        # Queue MUST still contain exactly 1 job (no duplicate!)
        queue_after_second = load_scheduled_posts()
        assert len(queue_after_second) == 1
        assert queue_after_second[0]["scheduled_id"] == "sch-post-1"


@patch("core.publisher.load_scheduled_posts", return_value=[{"status": "pending"}, {"status": "done"}])
def test_get_queue(mock_load):
    queue = get_queue()
    assert len(queue) == 1
    assert queue[0]["status"] == "pending"


@patch("core.publisher.publish_item_local", return_value={"status": "success", "results": []})
@patch("core.publisher.save_scheduled_posts")
@patch("core.publisher.load_scheduled_posts")
def test_process_due_posts_publishes_current_and_expires_legacy(mock_load, mock_save, mock_publish):
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    posts = [
        {"status": "pending", "target_time": (now - timedelta(minutes=1)).isoformat()},
        {"status": "pending", "target_time": (now - timedelta(days=2)).isoformat()},
        {"status": "pending", "target_time": (now + timedelta(hours=1)).isoformat()},
    ]
    mock_load.side_effect = lambda: posts

    assert process_due_posts(now) == 1
    mock_publish.assert_called_once()
    assert posts[0]["status"] == "published"
    assert posts[1]["status"] == "expired"
    assert posts[2]["status"] == "pending"

def test_requires_public_url_for_instagram_targets():
    assert requires_public_url(["instagram_reel"]) is True
    assert requires_public_url(["instagram_story"]) is True
    assert requires_public_url(["instagram_feed"]) is True
    assert requires_public_url(["youtube_shorts"]) is False

def test_normalize_platform_aliases():
    assert normalize_platforms(["instagram", "reel", "story", "feed", "post", "instagram_post"]) == [
        "instagram_reel",
        "instagram_reel",
        "instagram_story",
        "instagram_feed",
        "instagram_feed",
        "instagram_feed",
    ]

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_uses_existing_url(mock_upload):
    url, error = resolve_public_media_url({
        "video_url": "https://cdn.example/video.mp4",
        "video_path": "local.mp4",
        "platforms": ["instagram_reel"],
    })
    assert url == "https://cdn.example/video.mp4"
    assert error is None
    mock_upload.assert_not_called()

@patch("core.publisher.upload_to_temporary_host", return_value="https://temp.example/video.mp4")
def test_resolve_public_media_url_uploads_for_instagram_path(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["instagram_story"],
    })
    assert url == "https://temp.example/video.mp4"
    assert error is None
    mock_upload.assert_called_once_with("local.mp4")

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_missing_media_for_instagram(mock_upload):
    url, error = resolve_public_media_url({"platforms": ["instagram_reel"]})
    assert url is None
    assert error["error"] == "PUBLIC_URL_REQUIRED"
    mock_upload.assert_not_called()

@patch("core.publisher.upload_to_temporary_host", return_value=None)
def test_resolve_public_media_url_upload_failure(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["instagram_feed"],
    })
    assert url is None
    assert error["error"] == "TEMPORARY_UPLOAD_FAILED"
    mock_upload.assert_called_once_with("local.mp4")

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_does_not_upload_for_youtube(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["youtube_shorts"],
    })
    assert url is None
    assert error is None
    mock_upload.assert_not_called()

@patch("requests.get")
@patch("requests.post")
def test_upload_to_temporary_host_catbox(mock_post, mock_get):
    mock_post.return_value.status_code = 200
    mock_post.return_value.text = "https://files.catbox.moe/test.mp4"
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://files.catbox.moe/test.mp4"
    mock_get.assert_not_called()

@patch("requests.get", side_effect=Exception("Fail"))
@patch("requests.post")
@patch("time.sleep")
def test_upload_to_temporary_host_uguu(mock_sleep, mock_post, mock_get):
    mock_post.side_effect = [
        Exception("Catbox fail 1"),
        Exception("Catbox fail 2"),
        Exception("Catbox fail 3"),
        Exception("Litterbox fail"),
        MagicMock(status_code=200, text="https://uguu.se/test.mp4"),
    ]
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://uguu.se/test.mp4"

@patch("requests.get")
@patch("requests.post")
@patch("time.sleep")
def test_upload_to_temporary_host_gofile_last(mock_sleep, mock_post, mock_get):
    mock_get.return_value.json.return_value = {"status": "ok", "data": {"servers": [{"name": "srv1"}]}}
    gofile_response = MagicMock(status_code=200)
    gofile_response.json.return_value = {"status": "ok", "data": {"downloadPage": "http://gofile.io/123"}}
    mock_post.side_effect = [
        Exception("Catbox fail 1"),
        Exception("Catbox fail 2"),
        Exception("Catbox fail 3"),
        Exception("Litterbox fail"),
        Exception("Uguu fail"),
        gofile_response,
    ]
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "http://gofile.io/123"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value=None)
@patch("core.publisher.log_print")
def test_publish_item_local_upload_fail(mock_log, mock_upload, mock_post, mock_get_creds):
    post = {"video_path": "test.mp4", "caption": "test", "platforms": ["instagram_reel"]}
    result = publish_item_local(post)
    assert result["status"] == "error"
    assert result["error"] == "TEMPORARY_UPLOAD_FAILED"
    mock_log.assert_called_with("Temporary hosting did not return a public URL", "ERROR")

@patch("core.publisher.get_account_credentials")
@patch("core.publisher._upload_to_ig")
@patch("core.publisher.log_print")
def test_publish_item_local_does_not_call_ig_when_url_resolution_fails(mock_log, mock_ig, mock_get_creds):
    post = {"caption": "test", "platforms": ["instagram_reel"]}
    result = publish_item_local(post)
    assert result["status"] == "error"
    assert result["error"] == "PUBLIC_URL_REQUIRED"
    mock_ig.assert_not_called()
    mock_get_creds.assert_not_called()

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
        assert res["error"] == "IG_REQUEST_FAILED"

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

def test_is_platform_success_contract():
    from core.publisher import is_platform_success

    # Real success cases
    assert is_platform_success({"id": "178923456789"}) is True
    assert is_platform_success({"video_id": "v12345"}) is True
    assert is_platform_success({"id": "ig_123", "permalink": "https://ig/p/123"}) is True
    assert is_platform_success({"success": True}) is True

    # Error cases that must NEVER be treated as success
    assert is_platform_success({"error": "LIVE_MODE_RESTRICTION"}) is False
    assert is_platform_success({"error": "IG_CONTAINER_CREATE_FAILED", "message": "Failed"}) is False
    assert is_platform_success({"error_message": "Invalid OAuth access token"}) is False
    assert is_platform_success({"errors": [{"code": 100, "message": "Rate limit"}]}) is False
    assert is_platform_success({"error": "Page Access Token Missing"}) is False
    assert is_platform_success({"status_code": 400, "detail": "Bad request"}) is False
    assert is_platform_success({"status_code": 500, "id": "fake_id"}) is False
    assert is_platform_success({"success": False, "details": "Something failed"}) is False
    assert is_platform_success(None) is False
    assert is_platform_success({}) is False
    assert is_platform_success("truthy string") is False
    assert is_platform_success(["id", "123"]) is False


def test_save_scheduled_posts_atomic_replace_success(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import save_scheduled_posts, load_scheduled_posts
    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        initial_posts = [{"scheduled_id": "job-1", "status": "pending"}]
        save_scheduled_posts(initial_posts)

        assert target_file.exists()
        assert load_scheduled_posts() == initial_posts
        assert not (Path(td) / "scheduled_posts.json.tmp").exists()


def test_save_scheduled_posts_replace_failure_preserves_original(monkeypatch):
    import os
    import tempfile
    from pathlib import Path
    import pytest
    from core.publisher import save_scheduled_posts, load_scheduled_posts
    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        # Initial valid queue
        original_posts = [{"scheduled_id": "job-original", "status": "pending"}]
        save_scheduled_posts(original_posts)

        # Force os.replace to fail (e.g. permission or lock error)
        def mock_replace_fail(src, dst):
            raise PermissionError("Simulated file lock")

        monkeypatch.setattr(os, "replace", mock_replace_fail)
        monkeypatch.setattr("time.sleep", lambda s: None)

        with pytest.raises(PermissionError):
            save_scheduled_posts([{"scheduled_id": "job-corrupted", "status": "pending"}])

        # Ensure original queue remains pristine
        assert load_scheduled_posts() == original_posts

