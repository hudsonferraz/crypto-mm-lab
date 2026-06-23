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
