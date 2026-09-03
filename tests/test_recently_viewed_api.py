"""Tests for recently viewed notebooks and sources."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.notebook import Source


@pytest.fixture
def client():
    """Create test client after environment variables have been cleared by conftest."""
    from api.main import app

    return TestClient(app)


class TestRecentlyViewedApi:
    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)
    def test_recently_viewed_returns_mixed_items_newest_first(
        self, mock_repo_backend, client
    ):
        mock_repo_backend.side_effect = [
            [
                {
                    "id": "notebook:old",
                    "title": "Older Notebook",
                    "last_viewed_at": "2026-06-26T10:00:00Z",
                }
            ],
            [
                {
                    "id": "source:new",
                    "title": "Newer Source",
                    "last_viewed_at": "2026-06-27T10:00:00Z",
                }
            ],
        ]

        response = client.get("/api/recently-viewed")

        assert response.status_code == 200
        assert response.json() == [
            {
                "type": "source",
                "id": "source:new",
                "title": "Newer Source",
                "last_viewed_at": "2026-06-27T10:00:00Z",
            },
            {
                "type": "notebook",
                "id": "notebook:old",
                "title": "Older Notebook",
                "last_viewed_at": "2026-06-26T10:00:00Z",
            },
        ]

    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)
    def test_recently_viewed_honors_limit(self, mock_repo_backend, client):
        mock_repo_backend.side_effect = [
            [
                {
                    "id": "notebook:1",
                    "title": "Notebook 1",
                    "last_viewed_at": "2026-06-27T09:00:00Z",
                },
                {
                    "id": "notebook:2",
                    "title": "Notebook 2",
                    "last_viewed_at": "2026-06-27T07:00:00Z",
                },
            ],
            [
                {
                    "id": "source:1",
                    "title": "Source 1",
                    "last_viewed_at": "2026-06-27T10:00:00Z",
                },
                {
                    "id": "source:2",
                    "title": "Source 2",
                    "last_viewed_at": "2026-06-27T08:00:00Z",
                },
            ],
        ]

        response = client.get("/api/recently-viewed?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data] == ["source:1", "notebook:1"]
        assert len(data) == 2
        assert mock_repo_backend.await_args_list[0].kwargs["limit"] == 2
        assert mock_repo_backend.await_args_list[1].kwargs["limit"] == 2

    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)
    def test_recently_viewed_empty_when_no_view_history(
        self, mock_repo_backend, client
    ):
        mock_repo_backend.side_effect = [[], []]

        response = client.get("/api/recently-viewed")

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.notebooks.repo_relation_count", new_callable=AsyncMock)
    @patch("api.routers.notebooks.repo_update_record", new_callable=AsyncMock)
    @patch("api.routers.notebooks.Notebook.get", new_callable=AsyncMock)
    def test_get_notebook_stamps_last_viewed_at(
        self, mock_get, mock_update, mock_count, client
    ):
        from open_notebook.domain.notebook import Notebook

        mock_get.return_value = Notebook(
            id="notebook:1",
            name="Notebook",
            description="",
            archived=False,
            created="2026-06-27T09:00:00Z",
            updated="2026-06-27T09:00:00Z",
        )
        mock_count.return_value = 0

        response = client.get("/api/notebooks/notebook:1")

        assert response.status_code == 200
        mock_update.assert_awaited_once()
        assert mock_update.await_args.args[0] == "notebook:1"
        assert "last_viewed_at" in mock_update.await_args.args[1]

    @patch("api.routers.sources.repo_relations", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_update_record", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get_embedded_chunks", new_callable=AsyncMock)
    @patch("api.routers.sources.Source.get", new_callable=AsyncMock)
    def test_get_source_stamps_last_viewed_at(
        self, mock_get_source, mock_chunks, mock_update, mock_relations, client
    ):
        mock_get_source.return_value = Source(
            id="source:1",
            title="Source",
            topics=[],
            full_text="Source text",
            created="2026-06-27T09:00:00Z",
            updated="2026-06-27T09:00:00Z",
        )
        mock_chunks.return_value = 0
        mock_relations.return_value = []

        response = client.get("/api/sources/source:1")

        assert response.status_code == 200
        mock_update.assert_awaited_once()
        assert mock_update.await_args.args[0] == "source:1"
        assert "last_viewed_at" in mock_update.await_args.args[1]

    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)
    def test_recently_viewed_reorders_after_notebook_is_viewed_again(
        self, mock_repo_backend, client
    ):
        mock_repo_backend.side_effect = [
            [
                {
                    "id": "notebook:1",
                    "title": "Notebook",
                    "last_viewed_at": "2026-06-27T11:00:00Z",
                }
            ],
            [
                {
                    "id": "source:1",
                    "title": "Source",
                    "last_viewed_at": "2026-06-27T10:00:00Z",
                }
            ],
        ]

        response = client.get("/api/recently-viewed")

        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == ["notebook:1", "source:1"]
