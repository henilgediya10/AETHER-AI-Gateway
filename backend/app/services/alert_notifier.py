"""Send monitoring alerts to Slack without repeating the same alert too often."""
import httpx

COOLDOWN_SECONDS = 30 * 60  # do not repeat the same alert for 30 minutes


class SlackError(Exception):
    """Slack was unreachable or returned an error."""


def format_alert(alert: dict) -> str:
    severity = str(alert.get("severity", "warning")).upper()
    provider = alert.get("provider", "unknown")
    kind = alert.get("type")
    value = alert.get("value")
    threshold = alert.get("threshold")
    if kind == "high_error_rate":
        detail = f"error rate {value}% (limit {threshold}%)"
    elif kind == "high_latency":
        detail = f"p95 latency {value} ms (limit {threshold} ms)"
    else:
        detail = f"{kind}: {value} (limit {threshold})"
    return f"[{severity}] AETHER provider '{provider}': {detail}"


def send_slack(webhook_url: str, text: str) -> None:
    try:
        response = httpx.post(webhook_url, json={"text": text}, timeout=10)
    except httpx.HTTPError as exc:
        raise SlackError("Could not reach Slack") from exc
    if response.status_code != 200:
        raise SlackError(f"Slack returned status {response.status_code}")


def notify_alerts(alerts, *, redis_client, webhook_url, cooldown_seconds=COOLDOWN_SECONDS):
    result = {"sent": 0, "skipped": 0, "failed": 0}
    for alert in alerts:
        text = format_alert(alert)

        # Testing mode: no Slack URL, so only print.
        if not webhook_url:
            print(f"(Slack not configured) would send: {text}")
            result["skipped"] += 1
            continue

        key = f"aether:alert:{alert.get('provider')}:{alert.get('type')}"
        try:
            # nx=True means "only set if the key does not exist yet"
            claimed = bool(redis_client.set(key, "1", nx=True, ex=cooldown_seconds))
        except Exception:
            claimed = True  # Redis down: better a repeated alert than silence

        if not claimed:
            result["skipped"] += 1
            continue

        try:
            send_slack(webhook_url, text)
        except SlackError:
            try:
                redis_client.delete(key)  # let the next run try again
            except Exception:
                pass
            result["failed"] += 1
            continue

        result["sent"] += 1
    return result
