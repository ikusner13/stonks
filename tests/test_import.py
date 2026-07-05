from fastapi.testclient import TestClient

from app import config, db
from app.portfolio import holdings as holdings_mod
from app.portfolio.holdings import Holding, list_holdings
from app.schemas import Quote, TickerData
from app.web import app as web_app


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    holdings_mod.init_holdings_db()

    async def fetch_ticker_data(symbol: str) -> TickerData:
        return TickerData(
            symbol=symbol,
            fetched_at="2026-07-05T00:00:00Z",
            quote=Quote(price=10, currency="USD", change=0, change_percent=0),
        )

    monkeypatch.setattr(holdings_mod, "fetch_ticker_data", fetch_ticker_data)
    return TestClient(web_app.app)


def test_import_happy_path_accepts_bom_extra_column_and_mixed_case_header(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    csv = "\ufeffSyMbOl,ShArEs,AVG_COST,extra\naapl,2,150,ignored\nmsft,3,,ignored\n"

    response = client.post(
        "/portfolio/import",
        files={"file": ("holdings.csv", csv.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    assert "Imported 2 holdings" in response.text
    assert list_holdings() == [
        Holding(symbol="AAPL", shares=2, avg_cost=150),
        Holding(symbol="MSFT", shares=3, avg_cost=None),
    ]


def test_import_skips_bad_rows_with_line_numbers(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    csv = (
        "symbol,shares,avg_cost\n"
        "AAPL,abc,1\n"
        ",2,3\n"
        "MSFT,0,4\n"
        "GOOD,5,bad\n"
    )

    response = client.post(
        "/portfolio/import",
        files={"file": ("holdings.csv", csv.encode(), "text/csv")},
    )

    assert response.status_code == 200
    assert "Imported 1 holding" in response.text
    assert "line 2: bad shares &#39;abc&#39;" in response.text
    assert "line 3: missing symbol" in response.text
    assert "line 4: shares must be &gt; 0" in response.text
    assert list_holdings() == [Holding(symbol="GOOD", shares=5, avg_cost=None)]


def test_import_all_bad_rows_imports_nothing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    csv = "symbol,shares\nAAPL,nope\nMSFT,-1\n"

    response = client.post(
        "/portfolio/import",
        files={"file": ("holdings.csv", csv.encode(), "text/csv")},
    )

    assert response.status_code == 200
    assert "Imported 0 holdings" in response.text
    assert "No holdings yet" in response.text
    assert list_holdings() == []


def test_import_rejects_oversize_row_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    csv = "symbol,shares\n" + "\n".join(f"SYM{i},1" for i in range(501))

    response = client.post(
        "/portfolio/import",
        files={"file": ("holdings.csv", csv.encode(), "text/csv")},
    )

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "maximum 500 data rows" in response.text
    assert list_holdings() == []
