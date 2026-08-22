"""Small, explicit JWT boundary for the single-workspace demo.

The API never accepts a workspace id from a browser request.  It always comes
from the signed token (or from the development-only local identity).
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.config import get_settings

Role = Literal["operator", "reviewer", "admin"]
bearer_scheme = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    subject: str
    role: Role
    workspace_id: str
    development_identity: bool = False


class TokenRequest(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=512)


def _configured_users() -> list[dict]:
    raw = get_settings().jwt_users_json.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=503, detail="JWT_USERS_JSON is invalid") from exc
    return parsed if isinstance(parsed, list) else []


def issue_token(request: TokenRequest) -> dict:
    settings = get_settings()
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT login is not configured")
    user = next((item for item in _configured_users() if isinstance(item, dict) and item.get("username") == request.username), None)
    if not user or not isinstance(user.get("password"), str) or not hmac.compare_digest(user["password"], request.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    role = user.get("role")
    if role not in {"operator", "reviewer", "admin"}:
        raise HTTPException(status_code=503, detail="Configured JWT user has an invalid role")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.get("subject") or request.username,
        "role": role,
        "workspace_id": user.get("workspace_id") or settings.workspace_id,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),
    }
    return {"access_token": jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), "token_type": "bearer", "expires_in": settings.jwt_access_ttl_minutes * 60}


def get_current_principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    settings = get_settings()
    # Local development stays usable without silently becoming production auth.
    if not settings.jwt_secret and settings.environment in {"development", "test"}:
        return Principal(subject="development", role="admin", workspace_id=settings.workspace_id, development_identity=True)
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT_SECRET must be configured outside development")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token is required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"], audience=settings.jwt_audience, issuer=settings.jwt_issuer, options={"require": ["exp", "sub", "role", "workspace_id"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired bearer token", headers={"WWW-Authenticate": "Bearer"}) from exc
    try:
        return Principal(subject=str(claims["sub"]), role=claims["role"], workspace_id=str(claims["workspace_id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Bearer token has invalid claims") from exc


def require_roles(*allowed: Role):
    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status_code=403, detail="Your role cannot perform this action")
        return principal
    return dependency
