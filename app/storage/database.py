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


def create_db_engine(db_url: str) -> Engine:
    ensure_sqlite_parent_directory(db_url)
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(db_url, connect_args=connect_args)
