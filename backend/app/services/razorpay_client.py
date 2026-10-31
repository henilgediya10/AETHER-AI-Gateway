import os

import httpx

RAZORPAY_API_URL = "https://api.razorpay.com/v1/subscriptions"

# Which environment variable holds the Razorpay plan id for each of our plans
PLAN_ENV_VARS = {
    "pro": "RAZORPAY_PLAN_PRO",
    "enterprise": "RAZORPAY_PLAN_ENTERPRISE",
}


class BillingConfigError(Exception):
    """A key or plan id is missing in the environment."""


class RazorpayError(Exception):
    """Razorpay was unreachable or returned an error."""


def create_subscription(*, user_id, plan: str, total_count: int = 12) -> dict:
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    env_name = PLAN_ENV_VARS.get(plan)
    razorpay_plan_id = os.getenv(env_name) if env_name else None

    if not key_id or not key_secret or not razorpay_plan_id:
        raise BillingConfigError("Razorpay keys or plan id are not configured")

    payload = {
        "plan_id": razorpay_plan_id,
        "total_count": total_count,  # number of billing cycles (12 = 12 months)
        "customer_notify": 1,
        "notes": {"user_id": str(user_id), "plan": plan},
    }

    try:
        response = httpx.post(
            RAZORPAY_API_URL, json=payload, auth=(key_id, key_secret), timeout=15
        )
    except httpx.HTTPError as exc:
        raise RazorpayError("Could not reach Razorpay") from exc

    if response.status_code >= 400:
        raise RazorpayError(f"Razorpay returned status {response.status_code}")

    data = response.json()
    return {"subscription_id": data["id"], "payment_url": data.get("short_url")}
