from fastapi.testclient import TestClient
from main import app
from unittest.mock import patch

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "Central Publishing Hub Running"}

def test_get_queue():
    with patch("core.publisher.get_queue") as mock_queue:
        mock_queue.return_value = []
        response = client.get("/api/v1/queue")
        assert response.status_code == 200
        assert "pending" in response.json()

def test_search_locations_api():
    with patch("core.publisher.search_locations") as mock_search:
        mock_search.return_value = [{"id": "1", "name": "Loc"}]
        response = client.get("/api/v1/locations?q=test")
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1
