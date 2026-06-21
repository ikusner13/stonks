import logging

from fastapi import FastAPI, HTTPException

from .optimize import NoDataError, optimize
from .schemas import OptimizeRequest, OptimizeResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("portfolio-sidecar")

app = FastAPI(
    title="Portfolio Sidecar",
    version="0.1.0",
    summary="Mean-variance portfolio optimization (research context, not advice).",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Sync def -> FastAPI runs it in a threadpool, so the blocking yfinance/skfolio
# work doesn't stall the event loop.
@app.post("/optimize", response_model=OptimizeResponse)
def optimize_endpoint(req: OptimizeRequest) -> OptimizeResponse:
    symbols = [h.symbol for h in req.holdings]
    log.info("optimize %s objective=%s lookback=%dd", symbols, req.objective, req.lookback_days)
    try:
        return optimize(req)
    except NoDataError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — surface optimizer failures as 500s
        log.exception("optimize failed")
        raise HTTPException(status_code=500, detail=f"optimization failed: {e}") from e
