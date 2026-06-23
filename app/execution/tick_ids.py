from uuid import uuid4


def new_tick_id() -> str:
    return str(uuid4())
