from app.llm.discovery import _Filters, _passes


def test_sector_filter_rejects_mismatched_sector():
    filters = _Filters(sectors=["Healthcare"])

    assert not _passes(filters, market_cap=None, pe=None, sector="Financial Services")


def test_sector_filter_accepts_matching_sector_case_insensitive():
    filters = _Filters(sectors=["Healthcare"])

    assert _passes(filters, market_cap=None, pe=None, sector="Healthcare")
    assert _passes(filters, market_cap=None, pe=None, sector="healthcare")


def test_sector_filter_rejects_missing_sector():
    filters = _Filters(sectors=["Healthcare"])

    assert not _passes(filters, market_cap=None, pe=None, sector=None)


def test_missing_sector_passes_when_no_sector_filter():
    filters = _Filters(sectors=None)

    assert _passes(filters, market_cap=None, pe=None, sector=None)


def test_market_cap_filter_still_applies():
    filters = _Filters(max_market_cap=100.0)

    assert _passes(filters, market_cap=99.0, pe=None, sector=None)
    assert not _passes(filters, market_cap=101.0, pe=None, sector=None)


def test_pe_filter_still_applies():
    filters = _Filters(max_pe=20.0)

    assert _passes(filters, market_cap=None, pe=19.0, sector=None)
    assert not _passes(filters, market_cap=None, pe=21.0, sector=None)
