import json
import sys
from unittest.mock import patch
from faultcore.cli import main
from faultcore.branding import LOGO


def test_banner_and_no_arguments(capsys):
    assert main(["--banner"]) == 0
    assert LOGO in capsys.readouterr().out
    assert main([]) == 0
    assert "Reproduce. Minimize. Document." in capsys.readouterr().out


def test_help_and_version(capsys):
    assert main(["--help"]) == 0
    assert "--no-banner" in capsys.readouterr().out
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "faultcore 0.1.0"


def test_json_is_clean_on_interactive_terminal(capsys):
    with patch("faultcore.cli.sys.stdout.isatty", return_value=True), patch("faultcore.cli.cmd_run", side_effect=lambda args: (print('{"ok": true}'), 0)[1]):
        assert main(["run", "--arg", "unused", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_interactive_and_suppressed_banner(capsys):
    with patch("faultcore.cli.sys.stdout.isatty", return_value=True), patch("faultcore.cli.cmd_run", return_value=0):
        assert main(["run", "--arg", "unused"]) == 0
        assert LOGO in capsys.readouterr().out
        assert main(["--no-banner", "run", "--arg", "unused"]) == 0
        assert capsys.readouterr().out == ""
