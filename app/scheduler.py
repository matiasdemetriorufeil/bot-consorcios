"""Periodic jobs (APScheduler), run by the `scheduler` service of docker-compose.

    python -m app.scheduler

Times are Córdoba local time (settings.timezone). History lives in sync_runs.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.sync.canary import run_canary_job
from app.sync.nightly import AlreadyRunningError, run_nightly_job

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


def build_scheduler(timezone: str) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=timezone)
    options = {"coalesce": True, "max_instances": 1, "misfire_grace_time": MISFIRE_GRACE_SECONDS}
    scheduler.add_job(
        nightly_job, CronTrigger(timezone=timezone, **NIGHTLY_AT), id="nightly_sync", **options
    )
    scheduler.add_job(
        run_canary_job, CronTrigger(timezone=timezone, **CANARY_AT), id="canary", **options
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
