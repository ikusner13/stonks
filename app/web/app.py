"""FastAPI application shell for the JSON API and background scheduler."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import db
from ..alerts import init_alerts_db
from ..jobs import build_jobs, scheduler_loop
from ..portfolio.holdings import init_holdings_db
from ..portfolio.plan import init_targets_db
from ..portfolio.snapshots import init_snapshots_db
from ..portfolio.transactions import init_transactions_db

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task: asyncio.Task | None = None
    jobs_registry = build_jobs()
    if jobs_registry:
        task = asyncio.create_task(scheduler_loop(jobs_registry))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Stock Research API", lifespan=lifespan)

db.init_db()  # idempotent; ensures the watchlist table exists before first request
init_holdings_db()  # idempotent; ensures the holdings table exists before first request
init_snapshots_db()  # idempotent; ensures the NAV history table exists before first request
init_targets_db()  # idempotent; ensures target allocations exist before first request
init_transactions_db()  # idempotent; ensures the transaction ledger exists before first request
init_alerts_db()  # idempotent; ensures alert ranges and delivery ledger exist before first request

from .api import api_exception_handler, router as api_router  # noqa: E402

app.add_exception_handler(Exception, api_exception_handler)
app.include_router(api_router)
