from types import SimpleNamespace

from app.services.measured_plans import request_limits
from app.services.quota import _TEMP_FREE_DAILY_REQUEST_LIMIT, _TEMP_FREE_MONTHLY_REQUEST_LIMIT


def test_free_plan_is_temporarily_30_successful_requests_per_day():
    settings = SimpleNamespace(
        plan_monthly_request_units_free=30,
        plan_daily_request_units_free=6,
    )

    limits = request_limits(settings, "free")

    assert _TEMP_FREE_DAILY_REQUEST_LIMIT == 30
    assert _TEMP_FREE_MONTHLY_REQUEST_LIMIT == 930
    assert limits["daily_request_units"] == 30
    assert limits["monthly_request_units"] == 930


def test_free_plan_env_can_raise_but_not_reduce_temporary_allowance():
    settings = SimpleNamespace(
        plan_monthly_request_units_free=1200,
        plan_daily_request_units_free=40,
    )

    limits = request_limits(settings, "free")

    assert limits["daily_request_units"] == 40
    assert limits["monthly_request_units"] == 1200
