
"""Operations endpoints do not call an external AI provider."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app


def test_liveness():
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_success():
    required_tables = [
        "users",
        "revoked_tokens",
        "monthly_usage",
        "ai_usage_events",
    ]

    with (
        patch("app.main.engine") as engine,
        patch("app.main.inspect") as mock_inspect,
    ):
        mock_inspect.return_value.get_table_names.return_value = (
            required_tables
        )

        response = TestClient(app).get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

        engine.connect.assert_called_once()
        mock_inspect.assert_called_once()


def test_readiness_database_unavailable():
    with patch("app.main.engine") as engine:
        engine.connect.side_effect = OperationalError(
            "SELECT 1",
            {},
            Exception("down"),
        )

        response = TestClient(app).get("/health/ready")

        assert response.status_code == 503
        assert response.json() == {
            "detail": "Database unavailable"
        }
