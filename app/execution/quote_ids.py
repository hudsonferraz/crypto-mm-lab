from dataclasses import replace
from uuid import uuid4

from app.models.domain import Quote


def assign_quote_id(quote: Quote, quote_id: str | None = None) -> Quote:
    return replace(quote, quote_id=quote_id or str(uuid4()))
