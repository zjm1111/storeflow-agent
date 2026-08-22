import os


def test_jwt_login_and_workspace_claim(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    monkeypatch.setenv("JWT_USERS_JSON", '[{"username":"reviewer","password":"pw","role":"reviewer","workspace_id":"east"}]')
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.core.auth import TokenRequest, issue_token
    import jwt

    token = issue_token(TokenRequest(username="reviewer", password="pw"))["access_token"]
    claims = jwt.decode(token, "unit-test-secret", algorithms=["HS256"], audience="supplymind-web", issuer="supplymind")
    assert claims["workspace_id"] == "east"
    assert claims["role"] == "reviewer"
    get_settings.cache_clear()
