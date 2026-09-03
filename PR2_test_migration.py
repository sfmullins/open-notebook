#!/usr/bin/env python3
"""Migrate stale Surreal-query tests to PostgreSQL-native repository seams."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def migrate_chat_tests() -> None:
    rel = "tests/test_chat_routers_characterization.py"
    text = read(rel)
    text = text.replace("api.routers.chat.repo_query", "api.routers.chat.repo_relations")
    text = text.replace(
        "api.routers._chat_shared.repo_query",
        "api.routers._chat_shared.repo_relation_exists",
    )
    write(rel, text)


def migrate_config_tests() -> None:
    rel = "tests/test_config_endpoint_no_leak.py"
    text = read(rel).replace(
        "api.routers.config.repo_query", "api.routers.config.repo_healthcheck"
    )
    write(rel, text)


def migrate_domain_tests() -> None:
    rel = "tests/test_domain.py"
    text = read(rel)
    old = '''            patch(
                "open_notebook.domain.notebook.repo_query",
                new=AsyncMock(return_value=[]),
            ),
'''
    new = '''            patch(
                "open_notebook.domain.notebook.repo_relations",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "open_notebook.domain.notebook.repo_delete_relations",
                new=AsyncMock(return_value=None),
            ),
'''
    text = replace_once(text, old, new, "notebook delete repository mocks")
    write(rel, text)


def migrate_error_sanitization_tests() -> None:
    rel = "tests/test_error_message_sanitization.py"
    text = read(rel).replace(
        "api.routers.sources.repo_query", "api.routers.sources.repo_list"
    )
    write(rel, text)


def migrate_model_tests() -> None:
    rel = "tests/test_models_api.py"
    text = read(rel).replace(
        "open_notebook.database.repository.repo_query",
        "open_notebook.database.repository.repo_list",
    )
    text = text.replace("Mock repo_query", "Mock repo_list")
    write(rel, text)


def migrate_episode_model_resolution_tests() -> None:
    rel = "tests/test_podcast_episode_model_resolution.py"
    text = read(rel).replace(
        "open_notebook.ai.models.repo_query", "open_notebook.ai.models.repo_list"
    )
    old = '''        bound_vars = mock_query.call_args.args[1]
        assert len(bound_vars["model_ids"]) == 1
'''
    new = '''        mock_query.assert_awaited_once_with(
            "model", in_filters={"id": ["model:outline"]}
        )
'''
    text = replace_once(text, old, new, "model id de-dup assertion")
    write(rel, text)


def migrate_podcast_job_tests() -> None:
    rel = "tests/test_podcast_job_status_batching.py"
    text = read(rel).replace(
        "open_notebook.podcasts.models.repo_query",
        "open_notebook.podcasts.models.repo_command_rows",
    )
    old = '''        # Only the truthy id should reach the query's bound params.
        _, kwargs_or_args = mock_query.call_args
        bound_vars = mock_query.call_args.args[1]
        assert len(bound_vars["command_ids"]) == 1
'''
    new = '''        # Only the truthy id should reach the batched command lookup.
        mock_query.assert_awaited_once_with(["command:a"])
'''
    text = replace_once(text, old, new, "command id filtering assertion")
    write(rel, text)


def migrate_speaker_profile_tests() -> None:
    rel = "tests/test_podcast_speaker_profile.py"
    text = read(rel)

    insert_after = '''def make_input(speaker_profile=None):
    return PodcastGenerationInput(
        episode_profile="Test Episode Profile",
        speaker_profile=speaker_profile,
        episode_name="Test Episode",
        content="test content",
    )


'''
    fixture = '''def make_input(speaker_profile=None):
    return PodcastGenerationInput(
        episode_profile="Test Episode Profile",
        speaker_profile=speaker_profile,
        episode_name="Test Episode",
        content="test content",
    )


@pytest.fixture(autouse=True)
def _external_podcast_runtime_stub():
    """Unit tests do not require the operator-supplied FFmpeg runtime."""
    with patch(
        "commands.podcast_commands._load_podcast_creator",
        return_value=(Mock(), AsyncMock()),
    ):
        yield


'''
    text = replace_once(text, insert_after, fixture, "podcast runtime test fixture")

    text = text.replace(
        "open_notebook.podcasts.models.repo_query",
        "open_notebook.podcasts.models.repo_get",
    )
    old = '''            mock_query.return_value = [
                {
                    "id": "speaker_profile:abc",
                    "name": "Tech Experts",
                    "speakers": [
                        {
                            "name": "Alex",
                            "voice_id": "v1",
                            "backstory": "b",
                            "personality": "p",
                        }
                    ],
                }
            ]
'''
    new = '''            mock_query.return_value = {
                "id": "speaker_profile:abc",
                "name": "Tech Experts",
                "speakers": [
                    {
                        "name": "Alex",
                        "voice_id": "v1",
                        "backstory": "b",
                        "personality": "p",
                    }
                ],
            }
'''
    text = replace_once(text, old, new, "speaker record fixture")
    old = '''        assert mock_query.await_args is not None
        query, params = mock_query.await_args.args
        assert "FROM $id" in query
        assert str(params["id"]) == "speaker_profile:abc"
'''
    new = '''        mock_query.assert_awaited_once_with("speaker_profile:abc")
'''
    text = replace_once(text, old, new, "speaker record lookup assertion")

    text = text.replace("async def fake_repo_query(query, *args, **kwargs):", "async def fake_repo_list(table, *args, **kwargs):")
    text = text.replace('if "episode_profile" in query:', 'if table == "episode_profile":')
    text = text.replace(
        '"commands.podcast_commands.repo_query", new=fake_repo_query',
        '"commands.podcast_commands.repo_list", new=fake_repo_list',
    )
    old = '''            patch("commands.podcast_commands.configure", new=fake_configure),
            patch(
                "commands.podcast_commands.create_podcast",
                new=AsyncMock(
                    return_value={
                        "final_output_file_path": str(
                            tmp_path / "episodes" / "ep-dir" / "out.mp3"
                        ),
                        "transcript": {},
                        "outline": {},
                    }
                ),
            ),
'''
    new = '''            patch(
                "commands.podcast_commands._load_podcast_creator",
                return_value=(
                    fake_configure,
                    AsyncMock(
                        return_value={
                            "final_output_file_path": str(
                                tmp_path / "episodes" / "ep-dir" / "out.mp3"
                            ),
                            "transcript": {},
                            "outline": {},
                        }
                    ),
                ),
            ),
'''
    text = replace_once(text, old, new, "podcast creator injection")
    write(rel, text)


def migrate_recently_viewed_tests() -> None:
    rel = "tests/test_recently_viewed_api.py"
    text = read(rel).replace(
        "api.routers.notebooks.repo_query", "api.routers.notebooks.repo_list"
    )

    old = '''        assert mock_repo_query.await_args_list[0].args[1] == {"limit": 2}
        assert mock_repo_query.await_args_list[1].args[1] == {"limit": 2}
'''
    new = '''        assert mock_repo_query.await_args_list[0].kwargs["limit"] == 2
        assert mock_repo_query.await_args_list[1].kwargs["limit"] == 2
'''
    text = replace_once(text, old, new, "recently viewed limit assertion")

    start = text.index('    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)\n    def test_get_notebook_stamps_last_viewed_at')
    end = text.index('    @patch("api.routers.sources.Source.get_embedded_chunks"', start)
    replacement = '''    @patch("api.routers.notebooks.repo_relation_count", new_callable=AsyncMock)
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

'''
    text = text[:start] + replacement + text[end:]

    start = text.index('    @patch("api.routers.sources.Source.get_embedded_chunks"')
    end = text.index('    @patch("api.routers.notebooks.repo_list", new_callable=AsyncMock)\n    def test_recently_viewed_reorders', start)
    replacement = '''    @patch("api.routers.sources.repo_relations", new_callable=AsyncMock)
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

'''
    text = text[:start] + replacement + text[end:]
    write(rel, text)


def migrate_search_tests() -> None:
    rel = "tests/test_search_api.py"
    text = read(rel)
    marker = "class TestTextSearchHighlightOverflowFallback:"
    prefix = text[: text.index(marker)]
    replacement = '''class TestPostgresTextSearchDelegation:
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
'''
    write(rel, prefix + replacement)


def migrate_source_api_tests() -> None:
    rel = "tests/test_sources_api.py"
    text = read(rel).replace(
        "api.routers.sources.repo_query", "api.routers.sources.repo_relations"
    )
    text = text.replace(
        'mock_repo_query.return_value = ["notebook:1"]',
        'mock_repo_query.return_value = [{"out": "notebook:1"}]',
    )
    old = '''        # Regression guard: must query the reference edge by its `in` column
        called_query = mock_repo_query.await_args.args[0]
        assert "WHERE in = $source_id" in called_query
        assert "SELECT VALUE out FROM reference" in called_query
'''
    new = '''        # Regression guard: the source side of the reference relation is used.
        mock_repo_query.assert_awaited_once_with("reference", source="source:1")
'''
    text = replace_once(text, old, new, "retry relation assertion")

    marker = "class TestTitleSortUsesAlias:"
    prefix = text[: text.index(marker)]
    replacement = '''class TestTitleSortUsesPostgresRows:
    """Sorting happens over structured PostgreSQL repository rows."""

    @pytest.mark.asyncio
    @patch("api.routers.sources.repo_command_rows", new_callable=AsyncMock)
    @patch("api.routers.sources.count_source_embeddings", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_count", new_callable=AsyncMock)
    @patch("api.routers.sources.repo_list", new_callable=AsyncMock)
    async def test_sort_by_title_orders_structured_rows(
        self, mock_list, mock_count, mock_embeddings, mock_commands, client
    ):
        mock_list.return_value = [
            {"id": "source:z", "title": "Zulu", "created": "1", "updated": "1"},
            {"id": "source:a", "title": "Alpha", "created": "1", "updated": "1"},
        ]
        mock_count.return_value = 0
        mock_embeddings.return_value = 0
        mock_commands.return_value = []

        response = client.get("/api/sources?sort_by=title&sort_order=asc")

        assert response.status_code == 200
        assert [row["title"] for row in response.json()] == ["Alpha", "Zulu"]
        mock_list.assert_awaited_once_with("source")

    @pytest.mark.asyncio
    @patch("api.routers.sources.repo_list", new_callable=AsyncMock)
    async def test_all_sort_fields_return_200(self, mock_list, client):
        mock_list.return_value = []
        for field in ["type", "title", "created", "updated", "insights_count", "embedded"]:
            response = client.get(f"/api/sources?sort_by={field}")
            assert response.status_code == 200, f"sort_by={field}"

    def test_invalid_sort_field_returns_400(self, client):
        response = client.get("/api/sources?sort_by=bogus")
        assert response.status_code == 400
'''
    write(rel, prefix + replacement)


def migrate_typed_exception_tests() -> None:
    rel = "tests/test_typed_exceptions_reach_handlers.py"
    text = read(rel)
    replacements = {
        '"sources", "api.routers.sources.repo_query"': '"sources", "api.routers.sources.repo_list"',
        '"notebooks", "api.routers.notebooks.repo_query"': '"notebooks", "api.routers.notebooks.repo_list"',
        '"embedding_rebuild", "api.routers.embedding_rebuild.repo_query"': '"embedding_rebuild", "api.routers.embedding_rebuild.count_distinct_source_embeddings"',
        '"api.routers.sources.repo_query"': '"api.routers.sources.repo_list"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    write(rel, text)


def migrate_proxy_tests() -> None:
    rel = "tests/test_proxy.py"
    write(
        rel,
        '''"""Tests for local HTTP proxy bypass configuration."""

import os

import pytest

from open_notebook.utils.proxy import (
    INTERNAL_NO_PROXY_HOSTS,
    ensure_internal_no_proxy,
)

_PROXY_VARS = ("no_proxy", "NO_PROXY")


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_injects_internal_hosts_when_unset():
    ensure_internal_no_proxy()
    for var in _PROXY_VARS:
        value = os.environ[var]
        for host in INTERNAL_NO_PROXY_HOSTS:
            assert host in value.split(",")


def test_preserves_user_value(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.com,10.0.0.5")
    ensure_internal_no_proxy()
    entries = os.environ["NO_PROXY"].split(",")
    assert entries[:2] == ["example.com", "10.0.0.5"]
    for host in INTERNAL_NO_PROXY_HOSTS:
        assert host in entries


def test_does_not_duplicate_existing_hosts(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,example.com")
    ensure_internal_no_proxy()
    assert os.environ["NO_PROXY"].split(",").count("localhost") == 1


def test_lowercase_and_uppercase_kept_in_sync(monkeypatch):
    monkeypatch.setenv("no_proxy", "example.com")
    ensure_internal_no_proxy()
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_merges_both_case_variants(monkeypatch):
    monkeypatch.setenv("no_proxy", "lower.example.com")
    monkeypatch.setenv("NO_PROXY", "UPPER.example.com")
    ensure_internal_no_proxy()
    combined = os.environ["no_proxy"]
    assert "lower.example.com" in combined
    assert "UPPER.example.com" in combined
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_idempotent(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.com")
    ensure_internal_no_proxy()
    first = os.environ["NO_PROXY"]
    ensure_internal_no_proxy()
    assert os.environ["NO_PROXY"] == first


def test_wildcard_preserved(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    ensure_internal_no_proxy()
    assert os.environ["NO_PROXY"] == "*"


def test_wildcard_among_entries_preserved(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.com,*")
    ensure_internal_no_proxy()
    assert os.environ["NO_PROXY"] == "example.com,*"


def test_bypass_recognized_by_urllib(monkeypatch):
    import urllib.request

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.com:8080")
    ensure_internal_no_proxy()
    assert urllib.request.proxy_bypass("localhost:5055")
    assert urllib.request.proxy_bypass("host.docker.internal:8018")


def test_database_url_does_not_affect_http_proxy_bypass(monkeypatch):
    """PostgreSQL is not HTTP and must not mutate HTTP proxy exclusions."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@db.internal.corp:5432/open_notebook",
    )
    ensure_internal_no_proxy()
    entries = os.environ["NO_PROXY"].split(",")
    assert "db.internal.corp" not in entries
''',
    )


def fail_on_query_compat_residue() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        if "repo_query" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))
    if offenders:
        raise RuntimeError(
            "stale generic query compatibility references remain in tests: "
            + ", ".join(offenders)
        )


def main() -> None:
    migrate_chat_tests()
    migrate_config_tests()
    migrate_domain_tests()
    migrate_error_sanitization_tests()
    migrate_model_tests()
    migrate_episode_model_resolution_tests()
    migrate_podcast_job_tests()
    migrate_speaker_profile_tests()
    migrate_recently_viewed_tests()
    migrate_search_tests()
    migrate_source_api_tests()
    migrate_typed_exception_tests()
    migrate_proxy_tests()
    fail_on_query_compat_residue()


if __name__ == "__main__":
    main()
