"""The Breeze login stamps .env with its IST time; deploy/linux/breeze_login.sh skips a retry when that stamp is today."""
import importlib.util
import shutil
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _login_module(monkeypatch):
    fake = types.ModuleType("playwright.async_api")   # the module exits at import without Playwright
    fake.async_playwright = fake.Page = fake.Browser = object
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)
    spec = importlib.util.spec_from_file_location("breeze_auto_login", ROOT / "tools/breeze/breeze_auto_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_login_writes_one_ist_stamp_above_the_token(tmp_path, monkeypatch):
    mod = _login_module(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("BREEZE_API_KEY=k\n# BREEZE_SESSION_TOKEN last updated: 2026-10-14 08:25:40 IST\n"
                   "BREEZE_SESSION_TOKEN=old\nOTHER=1\n", encoding="utf-8")
    a = mod.BreezeLoginAutomator.__new__(mod.BreezeLoginAutomator)
    a.env_file_path, a.session_token = str(env), "new123"
    assert a.update_env_file()
    lines = env.read_text(encoding="utf-8").splitlines()
    stamps = [l for l in lines if l.startswith(mod.SESSION_STAMP)]
    assert len(stamps) == 1 and lines.index(stamps[0]) + 1 == lines.index("BREEZE_SESSION_TOKEN=new123")
    stamped = datetime.strptime(stamps[0][len(mod.SESSION_STAMP):], "%Y-%m-%d %H:%M:%S IST")
    assert stamped.date() == datetime.now(mod.IST).date()
    assert lines[0] == "BREEZE_API_KEY=k" and lines[-1] == "OTHER=1"


BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("stamp, force, skipped", [("today", "0", True), ("2026-10-14 08:25:40 IST", "0", False),
                                                   (None, "0", False), ("today", "1", False)])
def test_retry_skips_only_when_the_stamp_is_today(tmp_path, stamp, force, skipped):
    text = (ROOT / "deploy/linux/breeze_login.sh").read_text(encoding="utf-8")
    block = text[text.index("stamp=$(sed"):text.index("fi", text.index("stamp=$(sed")) + 2]
    env = tmp_path / ".env"
    line = "" if stamp is None else f"# BREEZE_SESSION_TOKEN last updated: {stamp}\n"
    env.write_text(line + "BREEZE_SESSION_TOKEN=x\n", encoding="utf-8")
    script = ("export TZ=Asia/Kolkata\n" + ("sed -i \"s/: today/: $(date '+%F %T') IST/\" .env\n") + block +
              "\necho WOULD_LOGIN\n")
    out = subprocess.run([BASH, "-c", script], cwd=tmp_path, env={"FORCE": force, "PATH": __import__("os").environ["PATH"]},
                         capture_output=True, text=True).stdout
    assert ("already refreshed today" in out) is skipped and ("WOULD_LOGIN" in out) is (not skipped)
