"""Tests for local HTTP proxy bypass configuration."""

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
