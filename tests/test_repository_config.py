from open_notebook.database.repository import (
    get_database_name,
    get_database_namespace,
    get_database_url,
)


def test_database_namespace_remains_compatibility_namespace():
    assert get_database_namespace() == "open_notebook"


def test_database_name_comes_from_database_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost:5432/custom_notebook?sslmode=disable",
    )
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    assert get_database_name() == "custom_notebook"


def test_database_url_falls_back_to_postgres_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql://user:password@localhost:5432/fallback_notebook"
    )

    assert get_database_url().endswith("/fallback_notebook")
