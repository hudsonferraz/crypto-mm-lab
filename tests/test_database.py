from app.storage.database import ensure_sqlite_parent_directory
from app.storage.repository import Repository


def test_create_db_engine_creates_missing_sqlite_parent_directory(tmp_path) -> None:
    data_dir = tmp_path / "data"
    db_url = f"sqlite:///{data_dir / 'mm_lab.db'}"

    assert not data_dir.exists()

    repo = Repository(db_url)
    repo.initialize()

    assert data_dir.exists()
    repo.close()


def test_ensure_sqlite_parent_directory_skips_in_memory_database() -> None:
    ensure_sqlite_parent_directory("sqlite:///:memory:")


def test_bare_postgres_urls_use_the_installed_driver() -> None:
    from app.storage.database import with_installed_postgres_driver

    url = "postgresql://mmlab:mmlab@postgres:5432/mmlab"
    assert with_installed_postgres_driver(url) == (
        "postgresql+psycopg2://mmlab:mmlab@postgres:5432/mmlab"
    )
    assert with_installed_postgres_driver("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    explicit = "postgresql+psycopg://u:p@h/db"
    assert with_installed_postgres_driver(explicit) == explicit
    assert with_installed_postgres_driver("sqlite:///./x.db") == "sqlite:///./x.db"
