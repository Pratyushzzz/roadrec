from fastapi import HTTPException


def err(status: int, code: str, message: str) -> HTTPException:
    """All API errors carry {detail: {code, message}} so clients can show a clear message."""
    return HTTPException(status, {"code": code, "message": message})
