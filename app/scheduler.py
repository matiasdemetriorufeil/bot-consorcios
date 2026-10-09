"""Periodic jobs (APScheduler), run by the `scheduler` service of docker-compose.

    python -m app.scheduler

Times are Córdoba local time (settings.timezone). History lives in sync_runs.
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.claims.jobs import JOB_MINUTES, NotConfiguredClient, run_claim_jobs
from app.claims.notify import Notifier
from app.config import get_settings
from app.db.session import SessionLocal
from app.sync.canary import run_canary_job
from app.sync.nightly import AlreadyRunningError, run_nightly_job
from app.whatsapp.client import WhatsAppError
from app.whatsapp.simulator import build_client

logger = logging.getLogger(__name__)

NIGHTLY_AT = {"hour": 3, "minute": 0}
# After the nightly sync has finished, during the day, when someone can react.
CANARY_AT = {"hour": 8, "minute": 0}
# A job missed while the container was down still runs if it restarts within this window.
MISFIRE_GRACE_SECONDS = 2 * 3600


def nightly_job() -> None:
    try:
        run_nightly_job()
    except AlreadyRunningError:
        logger.warning("Nightly sync skipped: another one is running")


@lru_cache(maxsize=1)
def _claims_notifier() -> Notifier:
    settings = get_settings()
    try:
        client = build_client(settings, SessionLocal)
    except WhatsAppError:
        logger.warning("Claims job: WhatsApp not configured, nothing will be sent")
        client = NotConfiguredClient()
    return Notifier(client, settings)


def claims_job() -> None:
    """Scheduled claims, reminders and the studio's alerts (app.claims.jobs)."""
    settings = get_settings()
    run_claim_jobs(SessionLocal, _claims_notifier(), datetime.now(UTC), settings.timezone)


def build_scheduler(timezone: str) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=timezone)
    options = {"coalesce": True, "max_instances": 1, "misfire_grace_time": MISFIRE_GRACE_SECONDS}
    scheduler.add_job(
        nightly_job, CronTrigger(timezone=timezone, **NIGHTLY_AT), id="nightly_sync", **options
    )
    scheduler.add_job(
        run_canary_job, CronTrigger(timezone=timezone, **CANARY_AT), id="canary", **options
    )
    # Every few minutes: a run missed is simply done by the next one.
    scheduler.add_job(
        claims_job,
        IntervalTrigger(minutes=JOB_MINUTES, timezone=timezone),
        id="claims",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=JOB_MINUTES * 60,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    scheduler = build_scheduler(get_settings().timezone)
    for job in scheduler.get_jobs():
        logger.info("Scheduled %s: %s", job.id, job.trigger)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
