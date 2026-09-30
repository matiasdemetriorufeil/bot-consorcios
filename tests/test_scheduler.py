from datetime import datetime
from zoneinfo import ZoneInfo

from app.scheduler import build_scheduler

CORDOBA = ZoneInfo("America/Argentina/Cordoba")


def next_run(job_id: str, now: datetime) -> datetime:
    job = build_scheduler("America/Argentina/Cordoba").get_job(job_id)
    return job.trigger.get_next_fire_time(None, now)


def test_nightly_sync_runs_daily_at_3_cordoba_time() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=CORDOBA)

    assert next_run("nightly_sync", now) == datetime(2026, 3, 11, 3, 0, tzinfo=CORDOBA)


def test_canary_runs_daily_after_the_nightly_sync() -> None:
    now = datetime(2026, 3, 10, 4, 0, tzinfo=CORDOBA)

    assert next_run("canary", now) == datetime(2026, 3, 10, 8, 0, tzinfo=CORDOBA)


def test_jobs_never_overlap_and_catch_up_after_a_restart() -> None:
    scheduler = build_scheduler("America/Argentina/Cordoba")

    for job in scheduler.get_jobs():
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time >= 3600
