"""Scheduler registration: ids, triggers, and interval values from config."""

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import main as main_mod


def _jobs(scheduler):
    return {j.id: j for j in scheduler.get_jobs()}


def test_schedule_jobs_registers_five(config_dict, monkeypatch):
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(main_mod, "scheduler", scheduler)
    main_mod.schedule_jobs()
    assert set(_jobs(scheduler)) == {
        "keep-alive", "smart-poll", "short-self-test", "long-self-test", "badblocks",
    }


def test_schedule_jobs_intervals_from_config(config_dict, monkeypatch):
    config_dict["scheduler"].update({
        "keep_alive_minutes": 4,
        "smart_poll_minutes": 15,
        "short_test_days": 7,
        "long_test_days": 30,
        "badblocks_days": 30,
    })
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(main_mod, "scheduler", scheduler)
    main_mod.schedule_jobs()
    jobs = _jobs(scheduler)
    assert jobs["keep-alive"].trigger.interval == timedelta(minutes=4)
    assert jobs["smart-poll"].trigger.interval == timedelta(minutes=15)
    assert jobs["short-self-test"].trigger.interval == timedelta(days=7)
    assert jobs["long-self-test"].trigger.interval == timedelta(days=30)
    assert jobs["badblocks"].trigger.interval == timedelta(days=30)


def test_schedule_jobs_every_job_guards_overlap(config_dict, monkeypatch):
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(main_mod, "scheduler", scheduler)
    main_mod.schedule_jobs()
    for job in _jobs(scheduler).values():
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time == 60


def test_schedule_jobs_badblocks_staggered(config_dict, monkeypatch):
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(main_mod, "scheduler", scheduler)
    main_mod.schedule_jobs()
    jobs = _jobs(scheduler)
    # heavy scan is pushed a day out so it never collides with the long test;
    # APScheduler normalizes a None start_date to "now", so compare offsets
    assert jobs["badblocks"].trigger.start_date is not None
    assert jobs["badblocks"].trigger.start_date > datetime.now(timezone.utc)
    assert jobs["keep-alive"].trigger.start_date is not None
    delta = jobs["badblocks"].trigger.start_date - jobs["keep-alive"].trigger.start_date
    assert delta >= timedelta(hours=23)
