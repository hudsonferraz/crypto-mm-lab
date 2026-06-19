from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def create_db_engine(db_url: str) -> Engine:
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(db_url, connect_args=connect_args)
