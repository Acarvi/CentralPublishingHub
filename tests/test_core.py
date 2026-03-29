import pytest
from unittest.mock import patch, MagicMock
from core.publisher import get_account_credentials, search_locations

def test_load_accounts():
    with patch("core.publisher.load_accounts") as mock_load:
        mock_load.return_value = {"test_brand": {"access_token": "abc"}}
        creds = get_account_credentials("test_brand")
        assert creds["access_token"] == "abc"

def test_search_locations_mock():
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"data": [{"id": "123", "name": "Madrid"}]}
        with patch("core.publisher.get_account_credentials") as mock_creds:
            mock_creds.return_value = {"access_token": "token"}
            results = search_locations("Madrid", "test_brand")
            assert len(results) == 1
            assert results[0]["name"] == "Madrid"
