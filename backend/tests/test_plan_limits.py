from app.services.usage import limit_for


def test_free_plan_limit():
    assert limit_for("free") == 50


def test_pro_plan_limit():
    assert limit_for("pro") == 2000


def test_enterprise_plan_limit():
    assert limit_for("enterprise") == 20000


def test_unknown_plan_uses_free_limit():
    assert limit_for("unknown") == 50
