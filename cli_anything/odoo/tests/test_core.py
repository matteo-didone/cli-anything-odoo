"""Unit tests — synthetic data, no Odoo server, no network.

Must pass on a bare checkout (CONTRIBUTING requirement).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from click.testing import CliRunner

from cli_anything.odoo.core import connection as conn_mod
from cli_anything.odoo.core import database as db_mod
from cli_anything.odoo.core import modules as mod_mod
from cli_anything.odoo.core import records as rec_mod
from cli_anything.odoo.core.session import Session, reset_session
from cli_anything.odoo.utils import output
from cli_anything.odoo.utils.domain import (build_domain, build_values, coerce,
                                            parse_filter, parse_ids, parse_literal,
                                            split_list)
from cli_anything.odoo.utils.rpc import OdooError, clean_fault, normalize_url


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class FakeRPC:
    """Records what would have gone over the wire."""

    def __init__(self, result=None, readonly=False, dry_run=False):
        self.calls = []
        self.result = result if result is not None else []
        self.readonly = readonly
        self.dry_run = dry_run
        self.profile_name = "test"
        self.url = "https://odoo.test"
        self.db = "testdb"
        self.username = "admin"
        self.password = "secret"
        self._uid = 2

    @property
    def uid(self):
        return self._uid

    def version(self):
        return {"server_serie": "18.0"}

    def execute_kw(self, model, method, args=None, kwargs=None, writes=False):
        self.calls.append((model, method, list(args or []), dict(kwargs or {}), writes))
        if self.dry_run and writes:
            return None
        if isinstance(self.result, dict):
            return self.result.get(method, [])
        return self.result

    def db_call(self, method, *args, writes=False):
        self.calls.append(("db", method, list(args), {}, writes))
        if self.dry_run and writes:
            return None
        return self.result

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def session_path(tmp_path):
    return str(tmp_path / "session.json")


@pytest.fixture(autouse=True)
def _clean_session():
    reset_session()
    yield
    reset_session()


def run_cli(argv, rpc=None, monkeypatch=None, session_path=None, input=None):
    """Invoke the real Click app with the RPC layer swapped out."""
    from cli_anything.odoo import odoo_cli

    rpc = rpc or FakeRPC()
    monkeypatch.setattr(odoo_cli, "build_client",
                        lambda **kw: _configured(rpc, kw))
    base = ["--session", session_path] if session_path else []
    result = CliRunner().invoke(odoo_cli.cli, base + argv, input=input)
    return result, rpc


def _configured(rpc, kwargs):
    rpc.dry_run = bool(kwargs.get("dry_run"))
    return rpc


# ---------------------------------------------------------------------------
# utils/domain.py
# ---------------------------------------------------------------------------

class TestFilterGrammar:
    @pytest.mark.parametrize("token,expected", [
        ("name=acme", ("name", "=", "acme")),
        ("id=42", ("id", "=", 42)),
        ("active=true", ("active", "=", True)),
        ("ref=null", ("ref", "=", None)),
        ("amount=3.5", ("amount", "=", 3.5)),
        ("name~acme", ("name", "ilike", "acme")),
        ("name!~test", ("name", "not ilike", "test")),
        ("state!=draft", ("state", "!=", "draft")),
        ("seq<5", ("seq", "<", 5)),
        ("seq>5", ("seq", ">", 5)),
        ("email:set", ("email", "!=", False)),
        ("email:unset", ("email", "=", False)),
    ])
    def test_operators(self, token, expected):
        assert parse_filter(token) == expected

    @pytest.mark.parametrize("token,expected", [
        ("a<=1", ("a", "<=", 1)),
        ("a>=1", ("a", ">=", 1)),
        ("a!=1", ("a", "!=", 1)),
        ("a!~x", ("a", "not ilike", "x")),
    ])
    def test_longest_token_wins(self, token, expected):
        """'<=' must not be read as '<'. This is the classic parser bug."""
        assert parse_filter(token) == expected

    def test_in_forms(self):
        assert parse_filter("id:in=1,2,3") == ("id", "in", [1, 2, 3])
        assert parse_filter("state:not-in=draft,sent") == ("state", "not in", ["draft", "sent"])
        assert parse_filter("parent_id:child-of=5") == ("parent_id", "child_of", 5)

    def test_value_containing_equals(self):
        assert parse_filter("note=a=b") == ("note", "=", "a=b")

    def test_malformed_raises_odoo_error(self):
        with pytest.raises(OdooError):
            parse_filter("nooperator")

    @pytest.mark.parametrize("raw", ["0431123456", "007", "00", "0123", "+0039", "-0431"])
    def test_leading_zero_stays_a_string(self, raw):
        """Found by the live E2E: '0431123456' became 431123456 and corrupted the record."""
        assert coerce(raw) == raw
        assert isinstance(coerce(raw), str)

    def test_plain_zero_and_decimals_still_convert(self):
        assert coerce("0") == 0 and isinstance(coerce("0"), int)
        assert coerce("0.5") == 0.5

    def test_coercion(self):
        assert coerce("42") == 42 and isinstance(coerce("42"), int)
        assert coerce("3.5") == 3.5
        assert coerce("true") is True and coerce("false") is False
        assert coerce("null") is None
        assert coerce("ciao") == "ciao"


class TestDomainAndValues:
    def test_json_and_python_domains(self):
        assert parse_literal('[["a","=",1]]') == [["a", "=", 1]]
        assert parse_literal("[('a','=',1)]") == [("a", "=", 1)]

    def test_domain_then_filters_in_order(self):
        built = build_domain(["b>2"], '[("a","=",1)]')
        assert built == [["a", "=", 1], ["b", ">", 2]]

    def test_domain_must_be_list(self):
        with pytest.raises(OdooError):
            build_domain(None, '{"a": 1}')

    def test_values_merge(self):
        assert build_values('{"name":"Acme"}', ["active=false"]) == {
            "name": "Acme", "active": False}

    def test_values_empty_raises(self):
        with pytest.raises(OdooError):
            build_values(None, None)

    def test_set_without_equals_raises(self):
        with pytest.raises(OdooError):
            build_values(None, ["broken"])

    def test_values_from_file(self, tmp_path):
        f = tmp_path / "v.json"
        f.write_text('{"password": "s3cr3t"}', encoding="utf-8")
        assert build_values(values_file=str(f)) == {"password": "s3cr3t"}

    def test_values_file_must_be_an_object(self, tmp_path):
        f = tmp_path / "v.json"
        f.write_text('[1,2]', encoding="utf-8")
        with pytest.raises(OdooError, match="object"):
            build_values(values_file=str(f))

    def test_values_file_missing_raises(self):
        with pytest.raises(OdooError, match="cannot read"):
            build_values(values_file="/nope/absent.json")

    def test_set_overrides_values_file(self, tmp_path):
        f = tmp_path / "v.json"
        f.write_text('{"a": 1, "b": 2}', encoding="utf-8")
        assert build_values(pairs=["b=9"], values_file=str(f)) == {"a": 1, "b": 9}

    def test_parse_ids(self):
        assert parse_ids("1,2 3") == [1, 2, 3]
        with pytest.raises(OdooError):
            parse_ids("1,abc")

    def test_split_list(self):
        assert split_list(["a,b", "c"]) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# utils/output.py and rpc helpers
# ---------------------------------------------------------------------------

class TestRendering:
    def test_many2one(self):
        assert output.cell([7, "Acme"]) == "Acme (7)"

    def test_false_is_blank(self):
        assert output.cell(False) == ""
        assert output.cell(None) == ""

    def test_truncation(self):
        assert output.cell("abcdefghij", 5) == "abcd…"

    def test_id_goes_first(self):
        assert output.columns_for([{"name": "a", "id": 1}])[0] == "id"

    def test_csv_respects_field_order(self):
        text = output.render_csv([{"id": 1, "name": "Acme"}], ["id", "name"])
        assert text.splitlines()[0] == "id,name"
        assert text.splitlines()[1] == "1,Acme"

    def test_redacts_sensitive_fields(self):
        assert output.redact({"password": "s3cr3t", "name": "Acme"}) == {
            "password": "***", "name": "Acme"}

    @pytest.mark.parametrize("field", ["password", "new_password", "api_key",
                                       "x_token", "client_secret", "otp"])
    def test_redaction_covers_the_usual_names(self, field):
        assert output.redact({field: "leak"})[field] == "***"

    def test_redaction_reaches_nested_values(self):
        assert output.redact([[41], {"password": "s3cr3t"}]) == [[41], {"password": "***"}]

    def test_empty_table(self):
        assert output.render_table([], ["id"]) == "(no records)"


class TestRpcHelpers:
    def test_fault_keeps_last_useful_line(self):
        import xmlrpc.client
        fault = xmlrpc.client.Fault(1, 'Traceback (most recent call last):\n'
                                       '  File "x", line 1\n'
                                       'odoo.exceptions.AccessError: Access denied')
        assert clean_fault(fault) == "odoo.exceptions.AccessError: Access denied"

    def test_url_gets_scheme(self):
        assert normalize_url("odoo.example.com") == "https://odoo.example.com"
        assert normalize_url("http://x/") == "http://x"

    def test_empty_url_raises(self):
        with pytest.raises(OdooError):
            normalize_url("")


# ---------------------------------------------------------------------------
# core/session.py
# ---------------------------------------------------------------------------

class TestSession:
    def test_fresh_has_no_connection(self, session_path):
        assert Session(session_path).has_connection() is False

    def test_round_trip(self, session_path):
        s = Session(session_path)
        s.set_connection(profile="p", url="https://x", db="d", username="u", uid=7)
        s.set_selection("res.partner", [1, 2, 3])
        s.save_session()

        again = Session(session_path)
        assert again.load_session() is True
        assert again.has_connection() is True
        assert again.data["uid"] == 7
        assert again.get_selection() == ("res.partner", [1, 2, 3])

    def test_saving_clears_modified_flag(self, session_path):
        s = Session(session_path)
        s.set_connection(url="https://x", db="d")
        assert s._modified is True
        s.save_session()
        assert s._modified is False

    def test_no_secret_is_ever_persisted(self, session_path):
        s = Session(session_path)
        s.set_connection(profile="p", url="https://x", db="d", username="u", uid=1)
        s.save_session()
        with open(session_path, encoding="utf-8") as fh:
            raw = fh.read()
        assert "password" not in raw.lower()
        assert "hunter2" not in raw

    def test_locked_save_truncates_a_larger_file(self, session_path):
        """The bug the guide warns about: leftover bytes from a longer previous file."""
        s = Session(session_path)
        s.set_selection("res.partner", list(range(500)))
        s.save_session()
        big = os.path.getsize(session_path)

        s.set_selection("res.partner", [1])
        s.save_session()
        assert os.path.getsize(session_path) < big
        with open(session_path, encoding="utf-8") as fh:
            json.load(fh)                       # still valid JSON, no trailing junk

    def test_clear(self, session_path):
        s = Session(session_path)
        s.set_connection(url="https://x", db="d")
        s.set_selection("res.partner", [1])
        s.clear()
        assert s.has_connection() is False
        assert s.get_selection() == (None, [])

    def test_load_missing_file_is_not_an_error(self, session_path):
        assert Session(session_path).load_session() is False


# ---------------------------------------------------------------------------
# core/connection.py
# ---------------------------------------------------------------------------

CONFIG = """\
[default]
url = https://config.example.com
db = config_db
username = config_user
password = config_secret

[locked]
url = https://ro.example.com
db = ro_db
username = ro_user
password = x
readonly = true
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(CONFIG, encoding="utf-8")
    return str(path)


class TestConnectionPrecedence:
    def test_config_is_used(self, config_file, monkeypatch):
        monkeypatch.delenv("ODOO_URL", raising=False)
        client = conn_mod.build_client(config=config_file)
        assert client.url == "https://config.example.com"
        assert client.db == "config_db"
        assert client.password == "config_secret"

    def test_env_beats_config(self, config_file, monkeypatch):
        monkeypatch.setenv("ODOO_URL", "https://env.example.com")
        client = conn_mod.build_client(config=config_file)
        assert client.url == "https://env.example.com"

    def test_flag_beats_env(self, config_file, monkeypatch):
        monkeypatch.setenv("ODOO_URL", "https://env.example.com")
        client = conn_mod.build_client(config=config_file, url="https://flag.example.com")
        assert client.url == "https://flag.example.com"

    def test_readonly_lands_on_client(self, config_file):
        assert conn_mod.build_client(profile="locked", config=config_file).readonly is True
        assert conn_mod.build_client(profile="default", config=config_file).readonly is False

    def test_missing_url_raises(self, tmp_path, monkeypatch):
        for var in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD", "ODOO_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(OdooError, match="no server URL"):
            conn_mod.build_client(config=str(tmp_path / "absent.ini"))

    def test_unknown_profile_lists_the_real_ones(self, config_file):
        with pytest.raises(OdooError, match="locked"):
            conn_mod.build_client(profile="nope", config=config_file)

    def test_password_file(self, tmp_path, monkeypatch):
        for var in ("ODOO_PASSWORD", "ODOO_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        secret = tmp_path / "key"
        secret.write_text("from-file\n", encoding="utf-8")
        assert conn_mod.resolve_secret({"password_file": str(secret)}) == "from-file"

    def test_password_command(self, monkeypatch):
        for var in ("ODOO_PASSWORD", "ODOO_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert conn_mod.resolve_secret({"password_command": "echo from-cmd"}) == "from-cmd"

    def test_env_secret_beats_config(self, config_file, monkeypatch):
        monkeypatch.setenv("ODOO_API_KEY", "from-env")
        assert conn_mod.build_client(config=config_file).password == "from-env"


# ---------------------------------------------------------------------------
# core/records.py — exact call construction
# ---------------------------------------------------------------------------

class TestRecordCalls:
    def test_search(self):
        rpc = FakeRPC()
        rec_mod.search(rpc, "res.partner", ["is_company=true"], limit=5)
        model, method, args, kwargs, writes = rpc.last
        assert (model, method) == ("res.partner", "search")
        assert args == [[["is_company", "=", True]]]
        assert kwargs == {"limit": 5}
        assert writes is False

    def test_search_read_fields(self):
        rpc = FakeRPC()
        rec_mod.search_read(rpc, "res.partner", fields=["name"], order="name asc")
        _, method, _, kwargs, _ = rpc.last
        assert method == "search_read"
        assert kwargs["fields"] == ["name"]
        assert kwargs["order"] == "name asc"

    def test_read_group_lazy_flag(self):
        rpc = FakeRPC()
        rec_mod.read_group(rpc, "sale.order", ["partner_id"], ["amount_total:sum"])
        assert rpc.last[3]["lazy"] is True
        rec_mod.read_group(rpc, "sale.order", ["partner_id", "state"], [])
        assert rpc.last[3]["lazy"] is False

    def test_read_group_needs_groupby(self):
        with pytest.raises(OdooError):
            rec_mod.read_group(FakeRPC(), "sale.order", [], [])

    @pytest.mark.parametrize("fn,extra", [
        (rec_mod.create, ({"name": "x"},)),
        (rec_mod.write, ("1", {"name": "x"})),
        (rec_mod.unlink, ("1",)),
    ])
    def test_mutations_are_tagged_as_writes(self, fn, extra):
        """--dry-run relies entirely on this flag."""
        rpc = FakeRPC()
        fn(rpc, "res.partner", *extra)
        assert rpc.last[4] is True

    def test_reads_are_not_tagged(self):
        rpc = FakeRPC()
        rec_mod.count(rpc, "res.partner")
        assert rpc.last[4] is False

    def test_call_marks_unknown_methods_as_writes(self):
        rpc = FakeRPC()
        rec_mod.call(rpc, "res.partner", "action_confirm")
        assert rpc.last[4] is True
        rec_mod.call(rpc, "res.partner", "name_search")
        assert rpc.last[4] is False

    def test_fields_get_filters_and_keeps_attributes(self):
        rpc = FakeRPC(result={"fields_get": {
            "name": {"type": "char", "string": "Name"},
            "email": {"type": "char", "string": "Email"},
        }})
        rows = rec_mod.fields_get(rpc, "res.partner", attributes=["type"], like="mail")
        assert rows == [{"name": "email", "type": "char"}]

    def test_context_is_forwarded(self):
        rpc = FakeRPC()
        rec_mod.count(rpc, "res.partner", context={"lang": "it_IT"})
        assert rpc.last[3]["context"] == {"lang": "it_IT"}


# ---------------------------------------------------------------------------
# core/modules.py and core/database.py
# ---------------------------------------------------------------------------

class TestModules:
    @pytest.mark.parametrize("action,method", [
        ("install", "button_immediate_install"),
        ("upgrade", "button_immediate_upgrade"),
        ("uninstall", "button_immediate_uninstall"),
    ])
    def test_action_maps_to_odoo_button(self, action, method):
        rpc = FakeRPC(result={"search": [11], "read": [{"name": "sale", "state": "installed"}]})
        mod_mod.act(rpc, action, "sale")
        assert any(c[1] == method and c[4] is True for c in rpc.calls)

    def test_unknown_action_raises(self):
        with pytest.raises(OdooError):
            mod_mod.act(FakeRPC(), "frobnicate", "sale")

    def test_missing_module_hints_at_update_list(self):
        rpc = FakeRPC(result={"search": []})
        with pytest.raises(OdooError, match="update-list"):
            mod_mod.find(rpc, "nope")

    def test_list_filters_by_state(self):
        rpc = FakeRPC()
        mod_mod.list_modules(rpc, like="sale", state="installed")
        assert rpc.last[2] == [[["name", "ilike", "sale"], ["state", "=", "installed"]]]


class TestDatabaseService:
    @pytest.mark.parametrize("fn,args", [
        (db_mod.create, ("newdb",)),
        (db_mod.duplicate, ("a", "b")),
        (db_mod.drop, ("gone",)),
        (db_mod.rename, ("a", "b")),
    ])
    def test_master_password_required_before_any_call(self, fn, args):
        rpc = FakeRPC()
        with pytest.raises(OdooError, match="master password"):
            fn(rpc, None, *args)
        assert rpc.calls == []              # nothing hit the network

    def test_list_needs_no_master_password(self):
        rpc = FakeRPC(result=["a", "b"])
        assert db_mod.list_databases(rpc) == ["a", "b"]


# ---------------------------------------------------------------------------
# CLI layer
# ---------------------------------------------------------------------------

class TestCliLayer:
    def test_json_flag_global(self, monkeypatch, session_path):
        rpc = FakeRPC(result=7)
        result, _ = run_cli(["--json", "record", "count", "res.partner"],
                            rpc, monkeypatch, session_path)
        assert result.exit_code == 0
        assert json.loads(result.output) == {"ok": True, "result": 7, "model": "res.partner"}

    def test_json_flag_per_command(self, monkeypatch, session_path):
        rpc = FakeRPC(result=7)
        result, _ = run_cli(["record", "count", "res.partner", "--json"],
                            rpc, monkeypatch, session_path)
        assert json.loads(result.output)["result"] == 7

    def test_filters_reach_the_orm(self, monkeypatch, session_path):
        rpc = FakeRPC(result=[{"id": 1, "name": "Acme"}])
        run_cli(["record", "list", "res.partner", "is_company=true", "name~acme",
                 "--fields", "name,email", "--limit", "5"], rpc, monkeypatch, session_path)
        model, method, args, kwargs, _ = rpc.last
        assert (model, method) == ("res.partner", "search_read")
        assert args == [[["is_company", "=", True], ["name", "ilike", "acme"]]]
        assert kwargs["fields"] == ["name", "email"] and kwargs["limit"] == 5

    def test_write_without_yes_sends_nothing(self, monkeypatch, session_path):
        rpc = FakeRPC()
        result, rpc = run_cli(["record", "write", "res.partner", "1", "--set", "name=X"],
                              rpc, monkeypatch, session_path)
        assert result.exit_code == 1
        assert "unconfirmed write" in result.output
        assert rpc.calls == []

    def test_write_with_yes_goes_through(self, monkeypatch, session_path):
        rpc = FakeRPC()
        result, rpc = run_cli(["-y", "record", "write", "res.partner", "1,2",
                               "--set", "name=X"], rpc, monkeypatch, session_path)
        assert result.exit_code == 0
        assert rpc.last[2] == [[1, 2], {"name": "X"}]

    def test_readonly_profile_refuses(self, monkeypatch, session_path):
        rpc = FakeRPC(readonly=True)
        result, rpc = run_cli(["-y", "record", "delete", "res.partner", "1"],
                              rpc, monkeypatch, session_path)
        assert result.exit_code == 1
        assert "read-only" in result.output
        assert rpc.calls == []

    def test_dry_run_never_echoes_a_password(self, monkeypatch, session_path):
        """Rehearsing a password change must not put it on screen."""
        from cli_anything.odoo import odoo_cli
        rpc = FakeRPC()
        monkeypatch.setattr(odoo_cli, "build_client", lambda **kw: _configured(rpc, kw))
        result = CliRunner().invoke(
            odoo_cli.cli,
            ["--session", session_path, "--dry-run", "-y", "record", "write",
             "res.users", "41", "--values-file", "-"],
            input='{"password": "s3cr3t"}')
        assert result.exit_code == 0
        assert "s3cr3t" not in result.output
        assert "***" in result.output

    def test_dry_run_performs_no_write(self, monkeypatch, session_path):
        rpc = FakeRPC()
        result, rpc = run_cli(["--dry-run", "record", "write", "res.partner", "1",
                               "--set", "name=X"], rpc, monkeypatch, session_path)
        assert result.exit_code == 0
        assert rpc.last[4] is True           # reached the layer...
        assert rpc.dry_run is True           # ...but the client swallowed it

    def test_selection_chains_through_at_sign(self, monkeypatch, session_path):
        rpc = FakeRPC(result=[10, 11])
        run_cli(["record", "search", "res.partner"], rpc, monkeypatch, session_path)
        rpc2 = FakeRPC(result=[{"id": 10}])
        run_cli(["record", "read", "res.partner", "@"], rpc2, monkeypatch, session_path)
        assert rpc2.last[2] == [[10, 11]]

    def test_empty_selection_raises(self, monkeypatch, session_path):
        result, rpc = run_cli(["record", "read", "res.partner", "@"],
                              FakeRPC(), monkeypatch, session_path)
        assert result.exit_code == 1
        assert "selection is empty" in result.output

    def test_selection_model_mismatch_is_caught(self, monkeypatch, session_path):
        run_cli(["record", "search", "res.partner"], FakeRPC(result=[1]),
                monkeypatch, session_path)
        result, _ = run_cli(["record", "read", "sale.order", "@"], FakeRPC(),
                            monkeypatch, session_path)
        assert result.exit_code == 1
        assert "res.partner ids" in result.output

    def test_error_shape_is_stable_under_json(self, monkeypatch, session_path):
        result, _ = run_cli(["--json", "record", "count", "res.partner", "broken"],
                            FakeRPC(), monkeypatch, session_path)
        payload = json.loads(result.output)
        assert payload["ok"] is False and "unrecognized filter" in payload["error"]

    def test_session_file_is_written_after_a_search(self, monkeypatch, session_path):
        run_cli(["record", "search", "res.partner"], FakeRPC(result=[1, 2]),
                monkeypatch, session_path)
        with open(session_path, encoding="utf-8") as fh:
            assert json.load(fh)["selection"]["ids"] == [1, 2]

    def test_values_from_stdin_keep_the_secret_out_of_argv(self, monkeypatch, session_path):
        """The whole point: nothing sensitive on the command line."""
        from cli_anything.odoo import odoo_cli
        rpc = FakeRPC()
        monkeypatch.setattr(odoo_cli, "build_client", lambda **kw: _configured(rpc, kw))
        argv = ["--session", session_path, "-y", "record", "write", "res.users", "41",
                "--values-file", "-"]
        result = CliRunner().invoke(odoo_cli.cli, argv, input='{"password": "s3cr3t"}')
        assert result.exit_code == 0
        assert rpc.last[2] == [[41], {"password": "s3cr3t"}]
        assert "s3cr3t" not in " ".join(argv)

    def test_dry_run_does_not_touch_the_session_file(self, monkeypatch, session_path):
        run_cli(["--dry-run", "record", "search", "res.partner"],
                FakeRPC(result=[1, 2]), monkeypatch, session_path)
        assert not os.path.exists(session_path)
