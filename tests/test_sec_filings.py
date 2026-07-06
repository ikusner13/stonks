from datetime import date

import httpx
import pytest

from app import alerts, config, db
from app.data import sec_filings
from app.portfolio.holdings import init_holdings_db


@pytest.fixture(autouse=True)
def _tmp_db_and_cache(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(sec_filings, "_SEC_TICKERS_TTL_MS", 60_000)
    monkeypatch.setattr(sec_filings, "_last_request_at", 0.0)
    monkeypatch.setattr(sec_filings, "_REQUEST_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    db.init_db()
    init_holdings_db()
    alerts.init_alerts_db()


async def test_fetch_company_filings_filters_stops_and_builds_urls(monkeypatch):
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-26-000001",
                    "0000320193-26-000002",
                    "0000320193-26-000003",
                    "0000320193-26-000004",
                    "0000320193-26-000005",
                ],
                "filingDate": [
                    "2026-07-03",
                    "2026-07-02",
                    "2026-07-01",
                    "2026-06-30",
                    "2026-07-04",
                ],
                "form": ["8-K", "10-Q", "4", "10-K", "8-K"],
                "primaryDocument": ["aapl-8k.htm", "", "x.htm", "old.htm", "after-old.htm"],
            }
        }
    }

    async def fake_request_json(url, *, params=None, client=None):
        assert url == "https://data.sec.gov/submissions/CIK0000320193.json"
        return payload

    monkeypatch.setattr(sec_filings, "_request_json", fake_request_json)

    filings = await sec_filings.fetch_company_filings("aapl", 320193, date(2026, 7, 1))

    assert [filing.accession for filing in filings] == [
        "0000320193-26-000001",
        "0000320193-26-000002",
    ]
    assert filings[0].url.endswith("/320193/000032019326000001/aapl-8k.htm")
    assert filings[1].url.endswith("/320193/000032019326000002")


async def test_fetch_ownership_filings_parses_skips_malformed_and_returns_empty_on_error(
    monkeypatch,
):
    captured: dict = {}
    payload = {
        "hits": {
            "hits": [
                {
                    "_id": "0001193125-26-000111:sc13g.htm",
                    "_source": {"file_date": "2026-07-05", "file_type": "SC 13G"},
                },
                {"_id": "missing-source"},
                {
                    "_id": "0001193125-26-000112:bad.htm",
                    "_source": {"file_date": "not-a-date", "file_type": "SC 13D"},
                },
            ]
        }
    }

    async def fake_request_json(url, *, params=None, client=None):
        captured["url"] = url
        captured["params"] = params
        return payload

    monkeypatch.setattr(sec_filings, "_request_json", fake_request_json)

    filings = await sec_filings.fetch_ownership_filings("AAPL", 320193, date(2026, 7, 1))

    assert captured["url"] == "https://efts.sec.gov/LATEST/search-index"
    assert captured["params"]["forms"] == "SC 13D,SC 13D/A,SC 13G,SC 13G/A"
    assert captured["params"]["ciks"] == "0000320193"
    assert [filing.accession for filing in filings] == ["0001193125-26-000111"]
    assert filings[0].form == "SC 13G"
    assert filings[0].url.endswith("/1193125/000119312526000111/sc13g.htm")

    async def failing_request_json(url, *, params=None, client=None):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(sec_filings, "_request_json", failing_request_json)

    assert await sec_filings.fetch_ownership_filings("AAPL", 320193, date(2026, 7, 1)) == []


async def test_ticker_cik_map_raises_on_http_failure_and_does_not_cache(monkeypatch):
    calls = 0

    async def failing_request_json(url, *, params=None, client=None):
        nonlocal calls
        calls += 1
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("GET", url),
            response=httpx.Response(500),
        )

    monkeypatch.setattr(sec_filings, "_request_json", failing_request_json)

    with pytest.raises(httpx.HTTPStatusError):
        await sec_filings.ticker_cik_map()
    with pytest.raises(httpx.HTTPStatusError):
        await sec_filings.ticker_cik_map()
    assert calls == 2


async def test_run_sec_filing_alerts_dedupes_across_runs(monkeypatch):
    db.add("AAPL")
    posts: list[str] = []

    async def fake_cik_map():
        return {"AAPL": 320193}

    async def fake_company(symbol, cik, since):
        return [
            sec_filings.NewFiling(
                symbol=symbol,
                cik=cik,
                form="8-K",
                filing_date="2026-07-03",
                accession="0000320193-26-000001",
                url="https://sec.test/aapl",
            )
        ]

    async def fake_ownership(symbol, cik, since):
        return [
            sec_filings.NewFiling(
                symbol=symbol,
                cik=cik,
                form="SC 13G",
                filing_date="2026-07-03",
                accession="0000320193-26-000001",
                url="https://sec.test/aapl-duplicate",
            )
        ]

    async def fake_post(message):
        posts.append(message)

    monkeypatch.setattr(alerts, "ticker_cik_map", fake_cik_map)
    monkeypatch.setattr(alerts, "fetch_company_filings", fake_company)
    monkeypatch.setattr(alerts, "fetch_ownership_filings", fake_ownership)
    monkeypatch.setattr(alerts, "post_discord", fake_post)

    assert await alerts.run_sec_filing_alerts() == {"alerts": 1}
    assert posts == ["SEC 8-K AAPL filed 2026-07-03 https://sec.test/aapl"]
    assert alerts.already_sent("sec_filing", "0000320193-26-000001")

    assert await alerts.run_sec_filing_alerts() == {"alerts": 0}
    assert len(posts) == 1


async def test_run_sec_filing_alerts_skips_failed_symbol_and_keeps_others(monkeypatch):
    db.add("AAPL")
    db.add("MSFT")
    posts: list[str] = []

    async def fake_cik_map():
        return {"AAPL": 320193, "MSFT": 789019}

    async def fake_company(symbol, cik, since):
        if symbol == "AAPL":
            raise RuntimeError("aapl unavailable")
        return [
            sec_filings.NewFiling(
                symbol=symbol,
                cik=cik,
                form="10-Q",
                filing_date="2026-07-04",
                accession="0000789019-26-000001",
                url="https://sec.test/msft",
            )
        ]

    async def fake_ownership(symbol, cik, since):
        return []

    async def fake_post(message):
        posts.append(message)

    monkeypatch.setattr(alerts, "ticker_cik_map", fake_cik_map)
    monkeypatch.setattr(alerts, "fetch_company_filings", fake_company)
    monkeypatch.setattr(alerts, "fetch_ownership_filings", fake_ownership)
    monkeypatch.setattr(alerts, "post_discord", fake_post)

    assert await alerts.run_sec_filing_alerts() == {"alerts": 1}
    assert posts == ["SEC 10-Q MSFT filed 2026-07-04 https://sec.test/msft"]


async def test_run_sec_filing_alerts_webhook_failure_marks_nothing(monkeypatch):
    db.add("AAPL")

    async def fake_cik_map():
        return {"AAPL": 320193}

    async def fake_company(symbol, cik, since):
        return [
            sec_filings.NewFiling(
                symbol=symbol,
                cik=cik,
                form="8-K",
                filing_date="2026-07-03",
                accession="0000320193-26-000002",
                url="https://sec.test/aapl",
            )
        ]

    async def fake_ownership(symbol, cik, since):
        return []

    async def fake_post(message):
        raise RuntimeError("webhook down")

    monkeypatch.setattr(alerts, "ticker_cik_map", fake_cik_map)
    monkeypatch.setattr(alerts, "fetch_company_filings", fake_company)
    monkeypatch.setattr(alerts, "fetch_ownership_filings", fake_ownership)
    monkeypatch.setattr(alerts, "post_discord", fake_post)

    assert await alerts.run_sec_filing_alerts() == {"alerts": 0}
    assert not alerts.already_sent("sec_filing", "0000320193-26-000002")


async def test_run_sec_filing_alerts_cik_map_failure_returns_early(monkeypatch):
    db.add("AAPL")
    posted = False

    async def fake_cik_map():
        raise RuntimeError("map unavailable")

    async def fake_post(message):
        nonlocal posted
        posted = True

    monkeypatch.setattr(alerts, "ticker_cik_map", fake_cik_map)
    monkeypatch.setattr(alerts, "post_discord", fake_post)

    assert await alerts.run_sec_filing_alerts() == {"alerts": 0}
    assert posted is False
