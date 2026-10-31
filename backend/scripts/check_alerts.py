"""Ask the running server for alerts and send them to Slack.

Run from the backend folder:
    python -m scripts.check_alerts          (normal check)
    python -m scripts.check_alerts --test   (send one test message to Slack)
"""
import os
import sys

import httpx
import redis

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.alert_notifier import SlackError, notify_alerts, send_slack


def main() -> int:
    slack_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()

    if "--test" in sys.argv:
        if not slack_url:
            print("SLACK_WEBHOOK_URL is empty in .env. Nothing to test.")
            return 1
        try:
            send_slack(slack_url, "AETHER test alert: the Slack connection works.")
        except SlackError as exc:
            print(f"Test failed: {exc}")
            return 1
        print("Test message sent. Check your Slack channel.")
        return 0

    token = os.getenv("AETHER_METRICS_TOKEN", "")
    metrics_url = os.getenv("ALERT_CHECK_URL", "http://127.0.0.1:8000/admin/metrics")
    if not token:
        print("AETHER_METRICS_TOKEN is not set. Cannot read the metrics.")
        return 1

    try:
        response = httpx.get(metrics_url, headers={"X-Metrics-Token": token}, timeout=10)
    except httpx.HTTPError:
        print("Could not reach the server. Is it running?")
        return 1
    if response.status_code != 200:
        print(f"Server answered with status {response.status_code}.")
        return 1

    alerts = response.json().get("alerts", [])
    print(f"Found {len(alerts)} alert(s).")
    if not alerts:
        return 0

    redis_client = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_timeout=3,
    )
    print(notify_alerts(alerts, redis_client=redis_client, webhook_url=slack_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
