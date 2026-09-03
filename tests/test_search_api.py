from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client after environment variables have been cleared by conftest."""
    from api.main import app

    return TestClient(app)


class TestSearchLimitValidation:
    """SearchRequest.limit must reject non-positive values (#863)."""

    @pytest.mark.parametrize("bad_limit", [0, -1, -100])
    def test_non_positive_limit_returns_422(self, bad_limit, client):
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": bad_limit},
        )
        assert response.status_code == 422

    def test_limit_above_max_returns_422(self, client):
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": 1001},
        )
        assert response.status_code == 422

    @patch("api.routers.search.text_search", new_callable=AsyncMock)
    def test_valid_limit_returns_200(self, mock_text_search, client):
        mock_text_search.return_value = []
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": 10},
        )
        assert response.status_code == 200
        mock_text_search.assert_awaited_once()


class TestPostgresTextSearchDelegation:
    """The domain wrapper delegates to PostgreSQL FTS and normalizes failures."""

    @pytest.mark.asyncio
    async def test_text_search_delegates_to_postgres_backend(self):
        from open_notebook.domain import notebook as notebook_module

        expected = [{"id": "source:1"}]
        with patch.object(
            notebook_module,
            "text_search_pg",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_search:
            result = await notebook_module.text_search("hello", 10)

        assert result == expected
        mock_search.assert_awaited_once_with("hello", 10, True, True)

    @pytest.mark.asyncio
    async def test_postgres_text_search_failure_is_wrapped(self):
        from open_notebook.domain import notebook as notebook_module
        from open_notebook.exceptions import DatabaseOperationError

        with patch.object(
            notebook_module,
            "text_search_pg",
            new_callable=AsyncMock,
            side_effect=RuntimeError("postgres text search failed"),
        ):
            with pytest.raises(DatabaseOperationError):
                await notebook_module.text_search("hello", 10)
