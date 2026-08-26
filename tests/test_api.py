import pytest
import tempfile
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from main import app
from unittest.mock import patch


@pytest.fixture(autouse=True)
def clean_posts_file(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))
        yield target_file

@pytest.mark.asyncio
async def test_read_root():
    # Use ASGITransport for newer httpx versions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "Central Publishing Hub Running"}

@pytest.mark.asyncio
async def test_health_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_get_queue_api():
    with patch("core.publisher.get_queue") as mock_queue:
        mock_queue.return_value = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/queue")
        assert response.status_code == 200
        assert "pending" in response.json()

@pytest.mark.asyncio
async def test_publish_api():
    payload = {
        "caption": "Test content",
        "platforms": ["instagram_reel"],
        "account_id": "test_account"
    }
    with patch("core.publisher.publish_item_local") as mock_publish:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/v1/publish", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

@pytest.mark.asyncio
async def test_schedule_api():
    payload = {
        "posts": [
            {
                "caption": "Test 1",
                "platforms": ["instagram_reel"],
                "account_id": "test"
            }
        ]
    }
    with patch("core.publisher.add_to_queue") as mock_queue:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/v1/schedule", json=payload)
            assert response.status_code == 200
            assert response.json()["status"] == "success"

@pytest.mark.asyncio
async def test_publish_now_api():
    payload = {
        "caption": "Now",
        "platforms": ["instagram_reel"],
        "video_path": "test.mp4"
    }
    with patch("core.publisher.publish_item_local") as mock_pub:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/v1/publish-now", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
