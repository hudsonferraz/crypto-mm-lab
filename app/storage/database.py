from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def ensure_sqlite_parent_directory(db_url: str) -> None:
    if not db_url.startswith("sqlite"):
        return
    if ":memory:" in db_url:
        return

    path_part = db_url.removeprefix("sqlite:///")
    if not path_part:
        return

    Path(path_part).parent.mkdir(parents=True, exist_ok=True)


def with_installed_postgres_driver(db_url: str) -> str:
    """Name the psycopg2 driver for a bare Postgres URL.

    psycopg2-binary is the installed driver. SQLAlchemy 2.0 read a bare
    `postgresql://` URL as psycopg2, 2.1 reads it as psycopg 3, which is not
    installed, so the Docker stack failed at startup after an upgrade.
    """
    for prefix in ("postgresql://", "postgres://"):
        if db_url.startswith(prefix):
            return "postgresql+psycopg2://" + db_url.removeprefix(prefix)
    return db_url


def create_db_engine(db_url: str) -> Engine:
    db_url = with_installed_postgres_driver(db_url)
    ensure_sqlite_parent_directory(db_url)
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(db_url, connect_args=connect_args)
