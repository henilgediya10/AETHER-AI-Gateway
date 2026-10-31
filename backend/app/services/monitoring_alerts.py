"""Evaluate provider metrics for error-rate and latency alerts."""


def evaluate_alerts(snapshot, min_attempts=10, max_error_rate=20, max_p95_ms=10000):
    alerts = []

    for provider, data in snapshot.get("providers", {}).items():
        attempts = data.get("total_attempts", 0)
        failures = data.get("failed_attempts", 0)
        p95 = data.get("success_latency_p95_ms")

        if attempts >= min_attempts:
            error_rate = failures / attempts * 100

            if error_rate > max_error_rate:
                alerts.append({
                    "provider": provider,
                    "type": "high_error_rate",
                    "severity": "critical",
                    "value": round(error_rate, 2),
                    "threshold": max_error_rate,
                })

        if p95 is not None and p95 > max_p95_ms:
            alerts.append({
                "provider": provider,
                "type": "high_latency",
                "severity": "warning",
                "value": p95,
                "threshold": max_p95_ms,
            })

    return alerts
