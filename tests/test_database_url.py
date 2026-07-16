from app.core.config import normalize_database_url


def test_railway_postgres_urls_use_psycopg_v3() -> None:
    assert normalize_database_url("postgres://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )
    assert normalize_database_url("postgresql://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )
    assert normalize_database_url("postgresql+psycopg2://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )


def test_psycopg_v3_url_is_unchanged() -> None:
    url = "postgresql+psycopg://user:pass@host:5432/db"

    assert normalize_database_url(url) == url
