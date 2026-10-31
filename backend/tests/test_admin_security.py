from fastapi.testclient import TestClient
from app.main import app


def test_admin_endpoints_require_login():
    client = TestClient(app)

    endpoints = [
        "/admin/overview",
        "/admin/users",
        "/admin/analytics/daily",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 401, (
            f"{endpoint}: expected 401, got {response.status_code}"
        )

def test_authenticated_non_admin_is_denied():
    import os
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routes.auth import get_current_user
    from app.models.user import User

    normal_user = User(
        id=99999,
        email="normal-user@example.com",
        password_hash="test-only",
        plan="free",
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = lambda: normal_user

    try:
        with patch.dict(os.environ, {"AETHER_ADMIN_EMAILS": "admin@example.com"}):
            client = TestClient(app)

            for endpoint in [
                "/admin/overview",
                "/admin/users",
                "/admin/analytics/daily",
            ]:
                response = client.get(endpoint)
                assert response.status_code == 403, (
                    f"{endpoint}: expected 403, got {response.status_code}"
                )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
