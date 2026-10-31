from app.services.monitoring_alerts import evaluate_alerts


def snapshot(attempts, failures, p95):
    return {
        "providers": {
            "gemini": {
                "total_attempts": attempts,
                "failed_attempts": failures,
                "success_latency_p95_ms": p95,
            }
        }
    }


def test_high_error_rate_alert():
    alerts = evaluate_alerts(snapshot(20, 10, 500))
    assert len(alerts) == 1
    assert alerts[0]["type"] == "high_error_rate"


def test_high_latency_alert():
    alerts = evaluate_alerts(snapshot(20, 0, 15000))
    assert len(alerts) == 1
    assert alerts[0]["type"] == "high_latency"


def test_healthy_provider():
    assert evaluate_alerts(snapshot(20, 0, 200)) == []


def test_insufficient_attempts():
    assert evaluate_alerts(snapshot(2, 2, 200)) == []
