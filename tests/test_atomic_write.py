"""Regressions for exclusive atomic write (no unsafe replace)."""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from faultcore.cli import main
from faultcore.report import (
    DestinationExistsError,
    FilesystemWriteError,
    UnsupportedWriteError,
    WriteError,
    atomic_write_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_existing_destination_unchanged(tmp_path):
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"ORIGINAL")
    with pytest.raises(DestinationExistsError):
        atomic_write_bytes(dest, b"NEW", overwrite=False)
    assert dest.read_bytes() == b"ORIGINAL"


def test_concurrent_create_before_link_preserves_other(tmp_path):
    """Another process creates dest after our empty check; link must fail, bytes intact."""
    dest = tmp_path / "race.bin"
    concurrent = b"CONCURRENT_BYTES"

    real_link = os.link

    def link_with_race(src, dst):
        # Simulate concurrent creator winning the name
        Path(dst).write_bytes(concurrent)
        return real_link(src, dst)  # should raise FileExistsError

    with mock.patch("faultcore.report.os.link", side_effect=link_with_race):
        with pytest.raises(DestinationExistsError):
            atomic_write_bytes(dest, b"OUR_CONTENT", overwrite=False)
    assert dest.read_bytes() == concurrent
    # no .faultcore- temps left
    temps = list(tmp_path.glob(".faultcore-*"))
    assert temps == []


def test_link_unsupported_no_replace(tmp_path):
    dest = tmp_path / "no_link.bin"
    concurrent = None

    def boom(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    with mock.patch("faultcore.report.os.link", side_effect=boom):
        with mock.patch("faultcore.report.os.replace") as repl:
            with pytest.raises(UnsupportedWriteError):
                atomic_write_bytes(dest, b"DATA", overwrite=False)
            repl.assert_not_called()
    assert not dest.exists()
    assert list(tmp_path.glob(".faultcore-*")) == []


def test_link_enosys_no_replace(tmp_path):
    dest = tmp_path / "enosys.bin"

    def boom(src, dst):
        raise OSError(errno.ENOSYS, "Function not implemented")

    with mock.patch("faultcore.report.os.link", side_effect=boom):
        with mock.patch("faultcore.report.os.replace") as repl:
            with pytest.raises(UnsupportedWriteError):
                atomic_write_bytes(dest, b"DATA", overwrite=False)
            repl.assert_not_called()
    assert not dest.exists()


def test_permission_error_on_temp(tmp_path):
    dest = tmp_path / "x.bin"

    def no_mkstemp(*a, **k):
        raise OSError(errno.EACCES, "Permission denied")

    with mock.patch("faultcore.report.tempfile.mkstemp", side_effect=no_mkstemp):
        with pytest.raises(FilesystemWriteError):
            atomic_write_bytes(dest, b"DATA", overwrite=False)
    assert not dest.exists()


def test_overwrite_true_uses_replace(tmp_path):
    dest = tmp_path / "ow.bin"
    dest.write_bytes(b"OLD")
    atomic_write_bytes(dest, b"NEW", overwrite=True)
    assert dest.read_bytes() == b"NEW"


def test_cli_existing_report_nonzero_and_input_intact(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rj = tmp_path / "r.json"
    rj.write_text("OLD_REPORT")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(rj),
    ])
    assert rc == 4
    assert rj.read_text() == "OLD_REPORT"
    assert inp.read_bytes() == b"CRASHME"


def test_cli_partial_save_message(tmp_path, capsys):
    """First report writes; second collides — report what was saved."""
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    good = tmp_path / "ok.json"
    same = tmp_path / "ok.json"  # intentional collision in preflight
    # collision detected before run
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(good), "--report-text", str(same),
    ])
    assert rc == 4
    assert not good.exists()
    assert inp.read_bytes() == b"CRASHME"


def test_temps_cleaned_on_eexist(tmp_path):
    dest = tmp_path / "d.bin"
    dest.write_bytes(b"OTHER")

    def eexist(src, dst):
        raise OSError(errno.EEXIST, "File exists")

    with mock.patch("faultcore.report.os.link", side_effect=eexist):
        # path.exists() already true at start — DestinationExistsError before temp
        with pytest.raises(DestinationExistsError):
            atomic_write_bytes(dest, b"X", overwrite=False)
    assert list(tmp_path.glob(".faultcore-*")) == []
    assert dest.read_bytes() == b"OTHER"
