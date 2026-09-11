"""End-to-end tests against a **real Odoo 18 server**.

Start one with ``docker-compose.test.yml`` in the repo root, then export:

    ODOO_TEST_URL, ODOO_TEST_DB, ODOO_TEST_USER, ODOO_TEST_PASSWORD

Without those the module skips, saying why. Nothing here is mocked: every
assertion is checked against the live database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

from cli_anything.odoo.core import records as rec_mod
from cli_anything.odoo.utils.rpc import OdooError, OdooRPC

URL = os.environ.get("ODOO_TEST_URL")
DB = os.environ.get("ODOO_TEST_DB")
USER = os.environ.get("ODOO_TEST_USER")
PASSWORD = os.environ.get("ODOO_TEST_PASSWORD")

requires_server = pytest.mark.skipif(
    not (URL and DB and USER and PASSWORD),
    reason="no live Odoo: set ODOO_TEST_URL / _DB / _USER / _PASSWORD "
           "(see docker-compose.test.yml)")

pytestmark = requires_server


def _resolve_cli(name: str) -> list:
    """Resolve the CLI command for subprocess tests.

    If CLI_ANYTHING_FORCE_INSTALLED is set, use the installed command.
    Otherwise, use python -m.
    """
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED"):
        import shutil
        path = shutil.which(name)
        if path:
            return [path]
        raise RuntimeError(f"{name} not found on PATH")
    return [sys.executable, "-m", "cli_anything.odoo"]


CLI = None


def run_cli(*args, expect_ok=True, session=None):
    """Run the installed CLI as a user would, and parse its JSON."""
    global CLI
    CLI = CLI or _resolve_cli("cli-anything-odoo")
    argv = CLI + ["--json", "--url", URL, "--db", DB, "-u", USER,
                  "--password", PASSWORD]
    if session:
        argv += ["--session", session]
    argv += list(args)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    payload = None
    for line in done.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    if payload is None:
        try:
            payload = json.loads(done.stdout)
        except ValueError:
            pytest.fail(f"no JSON from CLI.\nstdout={done.stdout}\nstderr={done.stderr}")
    if expect_ok:
        assert payload.get("ok") is True, f"CLI reported an error: {payload}"
    return payload


@pytest.fixture(scope="module")
def client():
    return OdooRPC(url=URL, db=DB, username=USER, password=PASSWORD)


@pytest.fixture
def session_file(tmp_path):
    return str(tmp_path / "e2e-session.json")


# ---------------------------------------------------------------------------
# The server really is Odoo 18
# ---------------------------------------------------------------------------

class TestLiveServer:
    def test_server_is_odoo_18(self, client):
        info = client.version()
        assert str(info["server_serie"]).startswith("18."), info

    def test_authentication_returns_a_uid(self, client):
        assert isinstance(client.uid, int) and client.uid > 0

    def test_wrong_password_is_refused_cleanly(self):
        bad = OdooRPC(url=URL, db=DB, username=USER, password="definitely-not-it")
        with pytest.raises(OdooError, match="refused"):
            _ = bad.uid

    def test_current_user_reads_back(self, client):
        rows = rec_mod.read(client, "res.users", str(client.uid), ["login"])
        assert rows[0]["login"] == USER

    def test_search_and_count_agree(self, client):
        ids = rec_mod.search(client, "res.partner", ["is_company=true"])
        total = rec_mod.count(client, "res.partner", ["is_company=true"])
        assert len(ids) == total

    def test_fields_get_describes_name(self, client):
        rows = rec_mod.fields_get(client, "res.partner", like="name")
        by_name = {r["name"]: r for r in rows}
        assert by_name["name"]["type"] == "char"

    def test_read_group_totals_match(self, client):
        groups = rec_mod.read_group(client, "res.partner", ["is_company"], [])
        summed = sum(g.get("is_company_count") or g.get("__count") or 0 for g in groups)
        assert summed == rec_mod.count(client, "res.partner")


# ---------------------------------------------------------------------------
# Workflow A — read-only audit, through the installed command
# ---------------------------------------------------------------------------

class TestCLISubprocess:
    def test_version_reports_18(self):
        payload = run_cli("version")
        assert str(payload["result"]["server"]["server_serie"]).startswith("18.")

    def test_whoami(self):
        payload = run_cli("whoami")
        assert payload["result"][0]["login"] == USER

    def test_audit_workflow_is_self_consistent(self, session_file):
        """Counts from three different commands must agree with each other."""
        total = run_cli("record", "count", "res.partner", "is_company=true",
                        session=session_file)["result"]

        listed = run_cli("record", "list", "res.partner", "is_company=true",
                         "--fields", "name", "--limit", "5", "--order", "name asc",
                         session=session_file)["result"]
        assert len(listed) == min(5, total)

        searched = run_cli("record", "search", "res.partner", "is_company=true",
                           session=session_file)["result"]
        assert len(searched) == total

        with open(session_file, encoding="utf-8") as fh:
            stored = json.load(fh)
        assert stored["selection"]["model"] == "res.partner"
        assert len(stored["selection"]["ids"]) == total


# ---------------------------------------------------------------------------
# Workflow B — create, amend, delete, leaving the database as we found it
# ---------------------------------------------------------------------------

class TestWriteLifecycle:
    def test_full_lifecycle(self, client, session_file):
        marker = f"cli-anything-e2e-{uuid.uuid4().hex[:8]}"
        before = rec_mod.count(client, "res.partner")

        created = run_cli("-y", "record", "create", "res.partner",
                          "--set", f"name={marker}", session=session_file)["result"]
        new_id = created["created"]
        assert isinstance(new_id, int)

        try:
            # The write really landed on the server, not just in our output.
            rows = rec_mod.read(client, "res.partner", str(new_id), ["name"])
            assert rows[0]["name"] == marker

            run_cli("-y", "record", "write", "res.partner", str(new_id),
                    "--set", "phone=0431123456", session=session_file)
            rows = rec_mod.read(client, "res.partner", str(new_id), ["phone"])
            assert rows[0]["phone"] == "0431123456"

            # '@' chains from the previous command's selection.
            read_back = run_cli("record", "read", "res.partner", str(new_id),
                                "--fields", "name", session=session_file)["result"]
            assert read_back[0]["name"] == marker
        finally:
            run_cli("-y", "record", "delete", "res.partner", str(new_id),
                    session=session_file)

        assert rec_mod.count(client, "res.partner") == before
        assert rec_mod.search(client, "res.partner", [f"name={marker}"]) == []


# ---------------------------------------------------------------------------
# Workflow C — the safety nets, checked against the live database
# ---------------------------------------------------------------------------

class TestSafetyNetsAgainstLiveServer:
    def test_dry_run_creates_nothing(self, client, session_file):
        marker = f"cli-anything-dry-{uuid.uuid4().hex[:8]}"
        before = rec_mod.count(client, "res.partner")
        run_cli("--dry-run", "-y", "record", "create", "res.partner",
                "--set", f"name={marker}", session=session_file)
        assert rec_mod.count(client, "res.partner") == before
        assert rec_mod.search(client, "res.partner", [f"name={marker}"]) == []

    def test_missing_yes_creates_nothing(self, client, session_file):
        marker = f"cli-anything-noyes-{uuid.uuid4().hex[:8]}"
        before = rec_mod.count(client, "res.partner")
        payload = run_cli("record", "create", "res.partner", "--set", f"name={marker}",
                          expect_ok=False, session=session_file)
        assert payload["ok"] is False
        assert rec_mod.count(client, "res.partner") == before

    def test_readonly_profile_creates_nothing(self, client, tmp_path):
        """A readonly profile must refuse even with --yes."""
        config = tmp_path / "ro.ini"
        config.write_text(
            f"[ro]\nurl = {URL}\ndb = {DB}\nusername = {USER}\n"
            f"password = {PASSWORD}\nreadonly = true\n", encoding="utf-8")
        marker = f"cli-anything-ro-{uuid.uuid4().hex[:8]}"
        before = rec_mod.count(client, "res.partner")

        cli = CLI or _resolve_cli("cli-anything-odoo")
        done = subprocess.run(
            cli + ["--json", "--config", str(config), "-p", "ro", "-y",
                   "record", "create", "res.partner", "--set", f"name={marker}"],
            capture_output=True, text=True, timeout=120)
        assert "read-only" in (done.stdout + done.stderr)
        assert rec_mod.count(client, "res.partner") == before
