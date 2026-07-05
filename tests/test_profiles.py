from app.profiles import LARGECAP, PENNY, select_profile
from app.schemas import Fundamentals, Quote, TickerData


def _ticker(
    *,
    exchange: str | None = None,
    price: float | None = 10.0,
    market_cap: float | None = 1_000_000_000,
) -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=price, currency="USD", change=0, change_percent=0)
        if price is not None
        else None,
        fundamentals=Fundamentals(exchange=exchange, market_cap=market_cap),
    )


def test_select_profile_manual_override_wins():
    profile, reason = select_profile(
        _ticker(exchange="PNK", price=1.0, market_cap=10_000_000),
        override="largecap",
    )

    assert profile is LARGECAP
    assert reason == "manual override"


def test_select_profile_otc_exchange():
    profile, reason = select_profile(_ticker(exchange="OQB"))

    assert profile is PENNY
    assert reason == "OTC-listed (OQB)"


def test_select_profile_price_under_five():
    profile, reason = select_profile(_ticker(price=4.99))

    assert profile is PENNY
    assert reason == "price $4.99 < $5"


def test_select_profile_market_cap_under_75m():
    profile, reason = select_profile(_ticker(market_cap=74_999_999))

    assert profile is PENNY
    assert reason == "market cap $74999999 < $75M"


def test_select_profile_defaults_to_largecap():
    profile, reason = select_profile(_ticker(exchange="NMS", price=10.0, market_cap=75_000_000))

    assert profile is LARGECAP
    assert reason == "default"


def test_select_profile_all_fields_missing_defaults_to_largecap():
    profile, reason = select_profile(
        TickerData(
            symbol="TST",
            fetched_at="2026-07-04T00:00:00Z",
            quote=None,
            fundamentals=Fundamentals(),
        )
    )

    assert profile is LARGECAP
    assert reason == "default"


def test_select_profile_first_matching_rule_wins_after_override():
    profile, reason = select_profile(_ticker(exchange="PNK", price=1.0, market_cap=10_000_000))

    assert profile is PENNY
    assert reason == "OTC-listed (PNK)"

    profile, reason = select_profile(_ticker(price=1.0, market_cap=10_000_000))

    assert profile is PENNY
    assert reason == "price $1 < $5"
