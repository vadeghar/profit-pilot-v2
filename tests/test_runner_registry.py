"""Tests for the cross-strategy RunnerRegistry (no network)."""
from execution.registry import RunnerRegistry


class FakeSession:
    def __init__(self, running=True, shape="status"):
        self._running = running
        self.shape = shape
        self.stop_calls = []

    def status(self):
        if self.shape == "status":
            return {"status": "RUNNING" if self._running else "STOPPED", "balance": 1000.0}
        return {"running": self._running, "ticks_seen": 42}  # OIPaperSession shape

    def stop(self, reason="manual"):
        self.stop_calls.append(reason)
        self._running = False


def teardown_function(function):
    # RunnerRegistry is a process-wide singleton; keep tests isolated.
    for entry in list(RunnerRegistry._sessions.values()):
        RunnerRegistry.unregister(entry["strategy_id"], entry["key"])


def test_register_and_list_all():
    sess = FakeSession()
    RunnerRegistry.register("scalp_writer_squeeze", "scalp_writer_squeeze", sess)
    listed = RunnerRegistry.list_all()
    assert len(listed) == 1
    assert listed[0]["strategy_id"] == "scalp_writer_squeeze"
    assert listed[0]["running"] is True
    assert listed[0]["detail"]["balance"] == 1000.0


def test_normalizes_oi_paper_session_running_shape():
    sess = FakeSession(running=True, shape="oi")
    RunnerRegistry.register("index_oi_momentum", "PAPER-123", sess)
    listed = RunnerRegistry.list_all()
    assert listed[0]["running"] is True
    assert listed[0]["detail"]["ticks_seen"] == 42


def test_unregister_removes_from_list():
    sess = FakeSession()
    RunnerRegistry.register("scalp_trap_fade", "scalp_trap_fade", sess)
    RunnerRegistry.unregister("scalp_trap_fade", "scalp_trap_fade")
    assert RunnerRegistry.list_all() == []


def test_get_returns_registered_session():
    sess = FakeSession()
    RunnerRegistry.register("scalp_pcr_velocity", "scalp_pcr_velocity", sess)
    assert RunnerRegistry.get("scalp_pcr_velocity", "scalp_pcr_velocity") is sess
    assert RunnerRegistry.get("scalp_pcr_velocity", "nonexistent") is None


def test_running_count_only_counts_running_sessions():
    RunnerRegistry.register("a", "a", FakeSession(running=True))
    RunnerRegistry.register("b", "b", FakeSession(running=False))
    assert RunnerRegistry.running_count() == 1


def test_stop_all_stops_every_registered_session():
    s1, s2 = FakeSession(), FakeSession()
    RunnerRegistry.register("a", "a", s1)
    RunnerRegistry.register("b", "b", s2)
    results = RunnerRegistry.stop_all(reason="emergency")
    assert len(results) == 2
    assert all(r["stopped"] for r in results)
    assert s1.stop_calls == ["emergency"] and s2.stop_calls == ["emergency"]
    assert RunnerRegistry.running_count() == 0


def test_stop_all_reports_failure_without_raising():
    class Broken:
        def status(self):
            return {"status": "RUNNING"}
        def stop(self, reason="manual"):
            raise RuntimeError("boom")
    RunnerRegistry.register("broken", "broken", Broken())
    results = RunnerRegistry.stop_all()
    assert results[0]["stopped"] is False
    assert "boom" in results[0]["error"]


def test_list_all_survives_a_broken_status_call():
    class Broken:
        def status(self):
            raise RuntimeError("status exploded")
        def stop(self, reason="manual"):
            pass
    RunnerRegistry.register("broken", "broken", Broken())
    listed = RunnerRegistry.list_all()
    assert listed[0]["running"] is False
    assert "status exploded" in listed[0]["detail"]["error"]
