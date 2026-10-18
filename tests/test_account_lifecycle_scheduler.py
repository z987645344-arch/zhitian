"""账号调度的业务日、跨重启补跑与原子状态；不等待真实日历时刻。"""
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import account_lifecycle as life, auth
from tests.test_account_lifecycle import accounts, mail


def controlled_clock(monkeypatch, current, *, wakeups=1):
    class Clock(datetime):
        value = current

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value

    class Stop:
        stopped = False

        def __init__(self):
            self.delays = []

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def clear(self):
            self.stopped = False

        def wait(self, seconds):
            self.delays.append(seconds)
            if self.stopped or len(self.delays) > wakeups:
                return True
            Clock.value += timedelta(seconds=seconds)
            return False

    stop = Stop()
    monkeypatch.setattr(life, "datetime", Clock)
    monkeypatch.setattr(life, "_stop", stop)
    monkeypatch.setattr(config, "ACCOUNT_LIFECYCLE_CATCH_UP_DELAY_SECONDS", 60)
    return Clock, stop


def state():
    return json.loads(life.scheduler_state_path().read_text(encoding="utf-8"))


def test_restart_across_four_catches_up_and_persists_business_day(monkeypatch):
    current = datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ)
    clock, stop = controlled_clock(monkeypatch, current)
    life._write_scheduler_success(date(2026, 10, 17))
    runs = []
    monkeypatch.setattr(life, "_run_daily", lambda now: runs.append(now))
    life._scheduler_work()
    assert stop.delays == [60, 86220]
    assert runs == [datetime(2026, 10, 17, 20, 3, tzinfo=timezone.utc)]
    assert state() == {"version": 1, "last_success_business_day": "2026-10-18",
                       "completed_at": runs[0].isoformat()}
    # 模拟新线程/新页面生命周期：只从磁盘读取成功日，不依赖内存标记。
    _, restarted = controlled_clock(monkeypatch, clock.value, wakeups=0)
    life._scheduler_work()
    assert len(runs) == 1
    assert restarted.delays == [86220]


def test_already_completed_business_day_waits_next_four(monkeypatch):
    controlled_clock(monkeypatch, datetime(2026, 10, 18, 8, tzinfo=life.TZ), wakeups=0)
    life._write_scheduler_success(date(2026, 10, 18))
    run = Mock()
    monkeypatch.setattr(life, "_run_daily", run)
    life._scheduler_work()
    run.assert_not_called()
    assert life._stop.delays == [20 * 3600]


def test_before_four_previous_business_day_complete_waits_today_four(monkeypatch):
    _, stop = controlled_clock(monkeypatch, datetime(2026, 10, 18, 3, 58, tzinfo=life.TZ))
    life._write_scheduler_success(date(2026, 10, 17))
    run = Mock()
    monkeypatch.setattr(life, "_run_daily", run)
    life._scheduler_work()
    assert stop.delays == [120, 86400]
    run.assert_called_once_with(datetime(2026, 10, 17, 20, tzinfo=timezone.utc))
    assert state()["last_success_business_day"] == "2026-10-18"


def test_first_start_without_record_catches_up_once_and_logs_no_identity(monkeypatch, caplog):
    controlled_clock(monkeypatch, datetime(2026, 10, 18, 12, tzinfo=life.TZ))
    assert not life.scheduler_state_path().exists()
    run = Mock()
    monkeypatch.setattr(life, "_run_daily", run)
    with caplog.at_level(logging.INFO, logger="account_lifecycle"):
        life._scheduler_work()
    run.assert_called_once()
    assert state()["last_success_business_day"] == "2026-10-18"
    events = [r.getMessage() for r in caplog.records if r.name == "account_lifecycle"]
    assert events == ["[account-lifecycle] catch_up_run business_day=2026-10-18"]


def test_failure_preserves_last_success_and_restart_retries(monkeypatch, caplog):
    current = datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ)
    controlled_clock(monkeypatch, current)
    life._write_scheduler_success(date(2026, 10, 17))
    original = life.scheduler_state_path().read_bytes()
    run = Mock(side_effect=RuntimeError("synthetic@example.com must not be logged"))
    monkeypatch.setattr(life, "_run_daily", run)
    with caplog.at_level(logging.ERROR, logger="account_lifecycle"):
        life._scheduler_work()
    assert life.scheduler_state_path().read_bytes() == original
    assert "daily_run_failed error_type=RuntimeError" in caplog.text
    assert "synthetic@example.com" not in caplog.text
    controlled_clock(monkeypatch, current)
    run.side_effect = None
    life._scheduler_work()
    assert run.call_count == 2
    assert state()["last_success_business_day"] == "2026-10-18"


def test_failure_without_prior_record_does_not_create_success(monkeypatch):
    controlled_clock(monkeypatch, datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ))
    monkeypatch.setattr(life, "_run_daily", Mock(side_effect=RuntimeError("synthetic")))
    life._scheduler_work()
    assert not life.scheduler_state_path().exists()


def test_catch_up_delay_is_configurable(monkeypatch):
    _, stop = controlled_clock(monkeypatch, datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ))
    monkeypatch.setattr(config, "ACCOUNT_LIFECYCLE_CATCH_UP_DELAY_SECONDS", 17)
    run = Mock()
    monkeypatch.setattr(life, "_run_daily", run)
    life._scheduler_work()
    assert stop.delays == [17, 86263]
    run.assert_called_once_with(datetime(2026, 10, 17, 20, 2, 17, tzinfo=timezone.utc))


def test_shutdown_during_startup_delay_does_not_run_or_mark_success(monkeypatch):
    _, stop = controlled_clock(monkeypatch, datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ), wakeups=0)
    run = Mock()
    monkeypatch.setattr(life, "_run_daily", run)
    life._scheduler_work()
    assert stop.delays == [60]
    run.assert_not_called()
    assert not life.scheduler_state_path().exists()


def test_success_state_is_atomic_and_replace_failure_preserves_previous(monkeypatch):
    controlled_clock(monkeypatch, datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ))
    life._write_scheduler_success(date(2026, 10, 17))
    path = life.scheduler_state_path()
    original = path.read_bytes()
    replacement = Mock(side_effect=OSError("synthetic"))
    monkeypatch.setattr(life, "os", SimpleNamespace(fdopen=os.fdopen, fsync=os.fsync,
        replace=replacement, path=os.path, unlink=os.unlink))
    with pytest.raises(OSError):
        life._write_scheduler_success(date(2026, 10, 18))
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".account-lifecycle-state-*"))
    source, target = replacement.call_args.args
    assert target == path and path.parent == type(path)(source).parent


def test_two_daily_calls_are_serialized_without_sleep(monkeypatch):
    attempting_second = threading.Event()
    first_entered = threading.Event()
    finish_first = threading.Event()
    real_lock = threading.Lock()
    count_lock = threading.Lock()
    attempts = 0
    active = 0
    peak = 0
    calls = []
    errors = []

    class ObservedLock:
        def __enter__(self):
            nonlocal attempts
            with count_lock:
                attempts += 1
                if attempts == 2:
                    attempting_second.set()
            real_lock.acquire()

        def __exit__(self, *args):
            real_lock.release()

    def body(now):
        nonlocal active, peak
        with count_lock:
            active += 1
            peak = max(peak, active)
            calls.append(now)
            first = len(calls) == 1
        if first:
            first_entered.set()
            if not finish_first.wait(10):
                raise RuntimeError("test_barrier_not_released")
        with count_lock:
            active -= 1

    def run():
        try:
            life.run_daily(datetime(2026, 10, 18, 12, tzinfo=life.TZ))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(life, "_daily_run_lock", ObservedLock())
    monkeypatch.setattr(life, "_run_daily", body)
    first, second = threading.Thread(target=run), threading.Thread(target=run)
    first.start()
    try:
        assert first_entered.wait(10)
        second.start()
        assert attempting_second.wait(10)
        assert len(calls) == 1
    finally:
        finish_first.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(calls) == 2 and peak == 1


def test_catch_up_is_after_startup_replay_and_uses_one_thread(monkeypatch):
    controlled_clock(monkeypatch, datetime(2026, 10, 18, 4, 2, tzinfo=life.TZ))
    events = []
    threads = []

    class Thread:
        def __init__(self, target, **kwargs):
            self.target, self.alive = target, False
            threads.append(self)

        def is_alive(self):
            return self.alive

        def start(self):
            self.alive = True
            self.target()

        def join(self):
            self.alive = False

    monkeypatch.setattr(life, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(life, "_thread", None)
    monkeypatch.setattr(life, "_run_daily", lambda now: events.append("daily"))
    monkeypatch.setattr(life, "reconcile_deleted_accounts", lambda: events.append("replay"))
    monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(main, "_accepting_requests", main._accepting_requests)
    with TestClient(main.app):
        life.start_scheduler()
        assert events == ["replay", "daily"]
        assert len(threads) == 1
    assert life._thread is None


def test_repeated_daily_runs_do_not_repeat_successful_inactivity_mail(accounts, mail):
    user = accounts()
    base = datetime(2025, 11, 18, 12, tzinfo=timezone.utc)
    now = life.add_months(base, 11)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET created_at=?, last_login_at=? WHERE user_id=?",
                     (base.isoformat(), base.isoformat(), user["user_id"]))
    life.run_daily(now)
    life.run_daily(now + timedelta(seconds=60))
    mail.assert_called_once_with(user["username"], "inactivity")
    assert life._state(user["user_id"])["inactivity_sent_at"] == now.isoformat()
    assert state()["last_success_business_day"] == "2026-10-18"
