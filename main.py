import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import poll_handling
from config_handling import get as get_config
from database_handling import init_db

log = logging.getLogger("uvicorn.error")

scheduler = AsyncIOScheduler()


def _add_job(fn, trigger, *, job_id, **kwargs) -> None:
    scheduler.add_job(
        fn,
        trigger,
        id=job_id,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        **kwargs,
    )


def schedule_jobs() -> None:
    """Register all recurring health jobs on the shared scheduler."""
    cfg = get_config()["scheduler"]
    now = datetime.now(timezone.utc)
    _add_job(poll_handling.keep_alive_read, "interval",
             minutes=cfg["keep_alive_minutes"], job_id="keep-alive")
    _add_job(poll_handling.poll_smart, "interval",
             minutes=cfg["smart_poll_minutes"], job_id="smart-poll")
    # weekly short test
    _add_job(poll_handling.run_short_tests, "interval",
             days=cfg["short_test_days"], job_id="short-self-test")
    # monthly long test
    _add_job(poll_handling.run_long_tests, "interval",
             days=cfg["long_test_days"], job_id="long-self-test")
    # monthly badblocks scan; stagger a day after the long test so the two
    # heavy scans dont compete on the same cadence
    _add_job(poll_handling.run_badblock_scans, "interval",
             days=cfg["badblocks_days"], start_date=now + timedelta(days=1),
             job_id="badblocks")


@asynccontextmanager
async def lifespan(app):
    init_db()
    poll_handling.discover_drives()
    schedule_jobs()
    scheduler.start()
    asyncio.create_task(poll_handling.apply_power_settings())
    log.info("scheduler started with %d jobs", len(scheduler.get_jobs()))
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="./frontend/src/"), name="static")
templates = Jinja2Templates(directory="./frontend/templates")


# read-only dashboard views (polled by the UI)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    drives = poll_handling.get_dashboard_status()
    refresh = get_config()["dashboard"]["refresh_seconds"]
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"request": request, "drives": drives, "refresh_seconds": refresh},
    )


@app.get("/dashboard/drives", response_class=HTMLResponse)
def dashboard_drives(request: Request):
    drives = poll_handling.get_dashboard_status()
    return templates.TemplateResponse(
        request, "_drives.html", {"request": request, "drives": drives}
    )


@app.post("/dashboard/test-all", response_class=HTMLResponse)
async def dashboard_test_all(request: Request):
    """Run SMART/temp poll, short self-test, and badblocks now; return the
    refreshed drives fragment. Tests run in the background."""
    asyncio.create_task(poll_handling.run_all_tests())
    drives = poll_handling.get_dashboard_status()
    return templates.TemplateResponse(
        request, "_drives.html", {"request": request, "drives": drives}
    )


@app.get("/test")
def test(db):
    return {"status": "ok"}
