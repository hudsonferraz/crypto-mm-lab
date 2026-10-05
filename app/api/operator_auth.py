from fastapi import Header, HTTPException

from app.config.settings import Settings


def require_operator_token(
    settings: Settings,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> None:
    expected = (settings.operator_api_token or "").strip()
    if not expected:
        return
    provided = (x_operator_token or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Operator token required")
