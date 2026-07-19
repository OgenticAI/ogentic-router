"""Unit tests for the audit sinks (OGE-584)."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ogentic_router.audit import (
    AuditUnavailableError,
    LocalFileSink,
    NoopSink,
    OgenticAuditSink,
    RouteDecisionAudit,
    safe_emit,
    sink_from_config,
)


def _row(request_id: str = "r0") -> RouteDecisionAudit:
    return RouteDecisionAudit(
        ts="2026-06-04T17:00:00Z",
        request_id=request_id,
        prompt_hash="sha256:abc",
        sensitivity_score=10,
        profile="shield-legal",
        top_category=None,
        groups_found=[],
        route_decision="openai-cloud",
        rule_id="low-cloud",
        transform=None,
        backend_is_local=False,
        latency_ms=1.0,
        error=None,
    )


def test_noopsink_is_silent_and_writes_nothing(tmp_path: Path) -> None:
    sink = NoopSink()
    # Never raises, returns None, produces no side effects.
    assert sink.emit(_row()) is None
    assert list(tmp_path.iterdir()) == []


def test_localfilesink_appends_one_json_line_per_row(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    sink = LocalFileSink(log)
    sink.emit(_row("a"))
    sink.emit(_row("b"))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["request_id"] == "a"
    assert json.loads(lines[1])["request_id"] == "b"


def test_localfilesink_appends_not_truncates(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    LocalFileSink(log).emit(_row("first"))
    # A brand-new sink instance on the same path must not clobber the file.
    LocalFileSink(log).emit(_row("second"))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["request_id"] for x in lines] == ["first", "second"]


def test_localfilesink_calls_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd) or real_fsync(fd))
    LocalFileSink(tmp_path / "a.jsonl").emit(_row())
    assert calls, "fsync was not called — durability guarantee broken"


def test_localfilesink_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # ~ resolves here
    sink = LocalFileSink("~/audit.jsonl")
    assert sink.path == tmp_path / "audit.jsonl"
    sink.emit(_row())
    assert (tmp_path / "audit.jsonl").exists()


def test_localfilesink_concurrent_writers_do_not_interleave(tmp_path: Path) -> None:
    """The file lock must keep rows whole under concurrent writers."""
    log = tmp_path / "audit.jsonl"
    n = 60

    def _write(i: int) -> None:
        LocalFileSink(log).emit(_row(f"r{i}"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(_write, range(n)))

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n
    ids = sorted(json.loads(x)["request_id"] for x in lines)  # every line valid JSON
    assert ids == sorted(f"r{i}" for i in range(n))


def test_safe_emit_swallows_sink_failures(caplog: pytest.LogCaptureFixture) -> None:
    class _Boom:
        def emit(self, row: RouteDecisionAudit) -> None:
            raise OSError("disk full")

    # Must not raise, and must log at WARNING.
    with caplog.at_level("WARNING"):
        safe_emit(_Boom(), _row())
    assert any("dropped a row" in r.message for r in caplog.records)


def test_ogenticauditsink_raises_without_the_library() -> None:
    with pytest.raises(AuditUnavailableError) as exc:
        OgenticAuditSink()
    assert "ogentic-router[audit]" in str(exc.value)
    assert isinstance(exc.value, ImportError)  # subclass contract


def test_sink_from_config_variants(tmp_path: Path) -> None:
    assert isinstance(sink_from_config(None), NoopSink)
    assert isinstance(sink_from_config({}), NoopSink)
    assert isinstance(sink_from_config({"sink": "noop"}), NoopSink)
    fs = sink_from_config({"sink": "local_file", "path": str(tmp_path / "a.jsonl")})
    assert isinstance(fs, LocalFileSink)


def test_sink_from_config_rejects_unknown_and_missing_path() -> None:
    with pytest.raises(ValueError, match="unknown audit.sink"):
        sink_from_config({"sink": "bogus"})
    with pytest.raises(ValueError, match="requires a 'path'"):
        sink_from_config({"sink": "local_file"})
