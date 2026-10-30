"""Pure policy tests; no external provider or database required."""
from app.services.usage import limit_for, PLAN_LIMITS, normalize_tokens

def test_free_plan_is_default_for_unknown():
    assert limit_for("unrecognized") == PLAN_LIMITS["free"]

def test_paid_plans_have_higher_limits():
    assert limit_for("free") < limit_for("pro") < limit_for("enterprise")


def test_normalize_provider_token_dictionary():
    assert normalize_tokens({"input": 10, "output": 20, "total": None}) == 30
    assert normalize_tokens({"total": 15}) == 15
    assert normalize_tokens(None) == 0
