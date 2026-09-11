"""cli-anything-odoo — agent-native CLI for Odoo 18.

One-shot commands for scripting, a REPL for interactive work, and ``--json``
everywhere so an agent never has to scrape a table.
"""

from __future__ import annotations

import functools
import json as _json
import os
import shlex
import sys

import click

from . import __version__
from .core import database as db_mod
from .core import modules as mod_mod
from .core import records as rec_mod
from .core.connection import build_client
from .core.session import get_session, reset_session
from .utils.domain import build_values, parse_ids, split_list, parse_literal
from .utils.output import emit, emit_error, redact
from .utils.rpc import OdooError

_repl_mode = False


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------

def json_option(fn):
    """Every command takes --json of its own, on top of the group's."""
    return click.option("-j", "--json", "cmd_json", is_flag=True, default=False,
                        help="Machine-readable JSON output")(fn)


def use_json(ctx, cmd_json=False):
    return bool(cmd_json or ctx.obj.get("use_json"))


def handle_errors(fn):
    """Expected failures become one clean line and exit 1, never a traceback."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ctx = click.get_current_context()
        try:
            return fn(*args, **kwargs)
        except OdooError as exc:
            emit_error(exc, use_json(ctx, kwargs.get("cmd_json")))
            if _repl_mode:
                return None
            sys.exit(1)
    return wrapper


def get_client(ctx, need_auth=True):
    """Build (once per process) the RPC client from flags, profile and session."""
    if ctx.obj.get("client") is not None:
        return ctx.obj["client"]
    opts = ctx.obj
    tracer = None
    if opts.get("verbose") or opts.get("dry_run"):
        tag = "dry-run" if opts.get("dry_run") else "rpc"

        def tracer(model, method, args, kwargs):
            click.echo(f"[{tag}] {model}.{method}"
                       f"(*{redact(args)!r}, **{redact(kwargs)!r})", err=True)

    client = build_client(
        profile=opts.get("profile"), config=opts.get("config"), url=opts.get("url"),
        db=opts.get("db"), username=opts.get("username"), password=opts.get("password"),
        timeout=opts.get("timeout"), insecure=opts.get("insecure"),
        dry_run=opts.get("dry_run"), on_call=tracer, need_auth=need_auth,
        session=get_session(opts.get("session_path")))
    ctx.obj["client"] = client
    return client


def guard_write(ctx, question):
    """Three nets before a write: readonly profile, dry-run, explicit consent."""
    opts = ctx.obj
    client = ctx.obj.get("client")
    if client is not None and getattr(client, "readonly", False) and not opts.get("dry_run"):
        raise OdooError(
            f"profile '{getattr(client, 'profile_name', '?')}' is read-only: refused. "
            "Remove readonly from the profile, or use --dry-run.")
    if opts.get("dry_run") or opts.get("yes"):
        return True
    if not sys.stdin.isatty():
        raise OdooError("unconfirmed write: add --yes (or use --dry-run).")
    return click.confirm(question, default=False)


def context_of(ctx, override=None):
    if override:
        parsed = parse_literal(override, "context")
        if not isinstance(parsed, dict):
            raise OdooError('--context must be an object, e.g. \'{"lang":"it_IT"}\'')
        return parsed
    return (get_session(ctx.obj.get("session_path")).data.get("context") or {}) or None


def remember(ctx, model, ids):
    """Keep the last result as the session's selection, for chained commands."""
    if not isinstance(ids, list):
        return
    sess = get_session(ctx.obj.get("session_path"))
    sess.snapshot()
    sess.set_selection(model, [i for i in ids if isinstance(i, int)])


def resolve_ids(ctx, model, raw):
    """'@' means 'whatever the last search selected'."""
    if raw and str(raw).strip() == "@":
        sel_model, ids = get_session(ctx.obj.get("session_path")).get_selection()
        if not ids:
            raise OdooError("selection is empty: run a search first, or pass ids.")
        if sel_model and model and sel_model != model:
            raise OdooError(f"selection holds {sel_model} ids, not {model}.")
        return ids
    return parse_ids(raw)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="cli-anything-odoo")
@click.option("--json", "use_json_flag", is_flag=True, help="Machine-readable JSON output")
@click.option("-p", "--profile", help="Profile from the config file")
@click.option("--config", help="Config file (default ~/.config/cli-anything-odoo/config.ini)")
@click.option("--session", "session_path", help="Session file holding connection + selection")
@click.option("--url", help="Server URL, e.g. https://odoo.example.com")
@click.option("--db", help="Database name")
@click.option("-u", "--user", "username", help="Login")
@click.option("--password", help="Password or API key (prefer the env var)")
@click.option("--timeout", type=float, help="Network timeout in seconds (default 120)")
@click.option("--insecure", is_flag=True, help="Skip TLS verification")
@click.option("-f", "--format", "fmt", type=click.Choice(["table", "csv"]), default="table")
@click.option("--width", type=int, default=40, help="Max column width")
@click.option("-v", "--verbose", is_flag=True, help="Trace every RPC call")
@click.option("--dry-run", is_flag=True, help="Run without writing anything")
@click.option("-y", "--yes", is_flag=True, help="Skip write confirmations")
@click.pass_context
def cli(ctx, use_json_flag, profile, config, session_path, url, db, username, password,
        timeout, insecure, fmt, width, verbose, dry_run, yes):
    """Drive Odoo 18 from the command line: ORM, modules and databases."""
    ctx.obj = {
        "use_json": use_json_flag, "profile": profile, "config": config,
        "session_path": session_path, "url": url, "db": db, "username": username,
        "password": password, "timeout": timeout, "insecure": insecure, "fmt": fmt,
        "width": width, "verbose": verbose, "dry_run": dry_run, "yes": yes,
        "client": None,
    }
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.result_callback()
@click.pass_context
def auto_save_on_exit(ctx, result, **kwargs):
    """Persist session state after one-shot commands (REPL saves on exit)."""
    if _repl_mode or ctx.obj.get("dry_run"):
        return
    sess = get_session(ctx.obj.get("session_path"))
    if sess._modified:
        try:
            sess.save_session()
        except Exception as exc:                      # never fail a good command
            click.echo(f"Warning: session auto-save failed: {exc}", err=True)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@cli.command("version")
@json_option
@click.pass_context
@handle_errors
def version_cmd(ctx, cmd_json):
    """Server and client versions."""
    client = get_client(ctx, need_auth=False)
    emit({"client": __version__, "server": client.version()},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@cli.command("whoami")
@json_option
@click.pass_context
@handle_errors
def whoami_cmd(ctx, cmd_json):
    """The authenticated user."""
    client = get_client(ctx)
    fields = ["id", "login", "name", "company_id", "lang", "tz"]
    rows = rec_mod.read(client, "res.users", str(client.uid), fields)
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], cols=fields, width=ctx.obj["width"])


@cli.command("connect")
@json_option
@click.pass_context
@handle_errors
def connect_cmd(ctx, cmd_json):
    """Authenticate and remember the connection in the session."""
    client = get_client(ctx)
    uid = client.uid
    sess = get_session(ctx.obj.get("session_path"))
    sess.snapshot()
    sess.set_connection(profile=getattr(client, "profile_name", None), url=client.url,
                        db=client.db, username=client.username, uid=uid)
    emit({"url": client.url, "db": client.db, "username": client.username, "uid": uid,
          "session": sess.path}, use_json(ctx, cmd_json), ctx.obj["fmt"],
         width=ctx.obj["width"])


@cli.command("status")
@json_option
@click.pass_context
@handle_errors
def status_cmd(ctx, cmd_json):
    """What the session currently points at."""
    sess = get_session(ctx.obj.get("session_path"))
    model, ids = sess.get_selection()
    emit({"session_file": sess.path, "profile": sess.data.get("profile"),
          "url": sess.data.get("url"), "db": sess.data.get("db"),
          "username": sess.data.get("username"), "uid": sess.data.get("uid"),
          "context": sess.data.get("context"),
          "selection": {"model": model, "count": len(ids), "ids": ids[:20]}},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@cli.command("reset")
@json_option
@click.pass_context
@handle_errors
def reset_cmd(ctx, cmd_json):
    """Forget connection and selection."""
    sess = get_session(ctx.obj.get("session_path"))
    sess.clear()
    emit({"cleared": True, "session_file": sess.path},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


# ---------------------------------------------------------------------------
# model: metadata
# ---------------------------------------------------------------------------

@cli.group("model")
def model_group():
    """Inspect models and fields before touching data."""


@model_group.command("list")
@click.option("--like", help="Filter on the technical name")
@click.option("-l", "--limit", type=int)
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def model_list(ctx, like, limit, context, cmd_json):
    """List models (ir.model)."""
    rows = rec_mod.list_models(get_client(ctx), like=like, limit=limit,
                               context=context_of(ctx, context))
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"],
         cols=["id", "model", "name", "transient"], width=ctx.obj["width"])


@model_group.command("fields")
@click.argument("model")
@click.option("--like", help="Filter on the field name")
@click.option("-a", "--attributes", multiple=True,
              help="Attributes to show (default type,string,required,readonly,relation)")
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def model_fields(ctx, model, like, attributes, context, cmd_json):
    """Field definitions of a model."""
    attrs = split_list(attributes) or None
    rows = rec_mod.fields_get(get_client(ctx), model, attributes=attrs, like=like,
                              context=context_of(ctx, context))
    cols = ["name"] + (attrs or ["type", "string", "required", "readonly", "relation"])
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], cols=cols, width=ctx.obj["width"])


# ---------------------------------------------------------------------------
# record: the ORM
# ---------------------------------------------------------------------------

@cli.group("record")
def record_group():
    """Read and change records."""


def query_options(fn):
    for option in reversed([
        click.argument("filters", nargs=-1),
        click.option("-d", "--domain", help="Full Odoo domain"),
        click.option("-l", "--limit", type=int),
        click.option("-o", "--offset", type=int),
        click.option("--order", help="e.g. 'name asc'"),
        click.option("-c", "--context", help='e.g. \'{"lang":"it_IT"}\''),
    ]):
        fn = option(fn)
    return fn


@record_group.command("search")
@click.argument("model")
@query_options
@json_option
@click.pass_context
@handle_errors
def record_search(ctx, model, filters, domain, limit, offset, order, context, cmd_json):
    """Ids only. The result becomes the session selection, reusable as '@'."""
    ids = rec_mod.search(get_client(ctx), model, filters, domain, limit, offset,
                         order, context_of(ctx, context))
    remember(ctx, model, ids)
    emit(ids, use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"],
         meta={"model": model, "count": len(ids or [])})


@record_group.command("count")
@click.argument("model")
@query_options
@json_option
@click.pass_context
@handle_errors
def record_count(ctx, model, filters, domain, limit, offset, order, context, cmd_json):
    """How many records match."""
    total = rec_mod.count(get_client(ctx), model, filters, domain, context_of(ctx, context))
    emit(total, use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"],
         meta={"model": model})


@record_group.command("list")
@click.argument("model")
@click.option("--fields", multiple=True, help="Comma-separated field names")
@query_options
@json_option
@click.pass_context
@handle_errors
def record_list(ctx, model, fields, filters, domain, limit, offset, order, context, cmd_json):
    """search_read: the everyday command."""
    cols = split_list(fields)
    rows = rec_mod.search_read(get_client(ctx), model, filters, domain, cols, limit,
                               offset, order, context_of(ctx, context))
    remember(ctx, model, [r.get("id") for r in rows or [] if isinstance(r, dict)])
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], cols=cols or None,
         width=ctx.obj["width"], meta={"model": model, "count": len(rows or [])})


@record_group.command("read")
@click.argument("model")
@click.argument("ids")
@click.option("--fields", multiple=True)
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def record_read(ctx, model, ids, fields, context, cmd_json):
    """Read records by id. IDS may be '@' for the current selection."""
    cols = split_list(fields)
    wanted = resolve_ids(ctx, model, ids)
    rows = rec_mod.read(get_client(ctx), model, ",".join(str(i) for i in wanted),
                        cols, context_of(ctx, context))
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], cols=cols or None,
         width=ctx.obj["width"], meta={"model": model})


@record_group.command("group")
@click.argument("model")
@click.option("--groupby", multiple=True, required=True)
@click.option("--fields", multiple=True, help="Aggregates, e.g. amount_total:sum")
@query_options
@json_option
@click.pass_context
@handle_errors
def record_group_cmd(ctx, model, groupby, fields, filters, domain, limit, offset,
                     order, context, cmd_json):
    """read_group: totals and counts, computed by the server."""
    rows = rec_mod.read_group(get_client(ctx), model, split_list(groupby),
                              split_list(fields), filters, domain, limit, offset,
                              order, context_of(ctx, context))
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"],
         meta={"model": model})


@record_group.command("create")
@click.argument("model")
@click.option("--values", help="JSON object of field values")
@click.option("--values-file", help="Read values from a JSON file, or '-' for stdin "
                                    "(keeps secrets out of the command line)")
@click.option("--set", "pairs", multiple=True, help="field=value, repeatable")
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def record_create(ctx, model, values, values_file, pairs, context, cmd_json):
    """Create one record."""
    payload = build_values(values, pairs, values_file)
    client = get_client(ctx)
    if not guard_write(ctx, f"Create a {model} record?"):
        raise OdooError("cancelled.")
    new_id = rec_mod.create(client, model, payload, context_of(ctx, context))
    emit({"model": model, "created": new_id, "values": redact(payload),
          "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@record_group.command("write")
@click.argument("model")
@click.argument("ids")
@click.option("--values")
@click.option("--values-file", help="Read values from a JSON file, or '-' for stdin "
                                    "(keeps secrets out of the command line)")
@click.option("--set", "pairs", multiple=True)
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def record_write(ctx, model, ids, values, values_file, pairs, context, cmd_json):
    """Update records. IDS may be '@'."""
    payload = build_values(values, pairs, values_file)
    wanted = resolve_ids(ctx, model, ids)
    client = get_client(ctx)
    if not guard_write(ctx, f"Write {', '.join(payload)} on {len(wanted)} {model} record(s)?"):
        raise OdooError("cancelled.")
    rec_mod.write(client, model, ",".join(str(i) for i in wanted), payload,
                  context_of(ctx, context))
    emit({"model": model, "written": wanted, "values": redact(payload),
          "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@record_group.command("delete")
@click.argument("model")
@click.argument("ids")
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def record_delete(ctx, model, ids, context, cmd_json):
    """Delete records permanently. IDS may be '@'."""
    wanted = resolve_ids(ctx, model, ids)
    client = get_client(ctx)
    if not guard_write(ctx, f"PERMANENTLY delete {len(wanted)} {model} record(s) {wanted}?"):
        raise OdooError("cancelled.")
    rec_mod.unlink(client, model, ",".join(str(i) for i in wanted), context_of(ctx, context))
    emit({"model": model, "deleted": wanted, "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@record_group.command("call")
@click.argument("model")
@click.argument("method")
@click.option("--args", help="JSON list of positional arguments")
@click.option("--kwargs", help="JSON object of keyword arguments")
@click.option("-c", "--context")
@json_option
@click.pass_context
@handle_errors
def record_call(ctx, model, method, args, kwargs, context, cmd_json):
    """Call any model method."""
    call_args = parse_literal(args, "args") if args else []
    if not isinstance(call_args, list):
        raise OdooError('--args must be a list, e.g. \'[[1,2]]\'')
    call_kwargs = parse_literal(kwargs, "kwargs") if kwargs else {}
    if not isinstance(call_kwargs, dict):
        raise OdooError('--kwargs must be an object, e.g. \'{"limit": 5}\'')
    client = get_client(ctx)
    if method not in rec_mod.READ_ONLY_METHODS:
        if not guard_write(ctx, f"Run {model}.{method}? It may modify data."):
            raise OdooError("cancelled.")
    result = rec_mod.call(client, model, method, call_args, call_kwargs,
                          context_of(ctx, context))
    emit(result, use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"],
         meta={"model": model, "method": method})


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------

@cli.group("module")
def module_group():
    """Install, upgrade and inspect Odoo modules."""


@module_group.command("list")
@click.option("--like", help="Filter on the technical name")
@click.option("--state", help="installed, uninstalled, to upgrade...")
@click.option("-l", "--limit", type=int)
@json_option
@click.pass_context
@handle_errors
def module_list(ctx, like, state, limit, cmd_json):
    """List modules."""
    rows = mod_mod.list_modules(get_client(ctx), like=like, state=state, limit=limit)
    emit(rows, use_json(ctx, cmd_json), ctx.obj["fmt"], cols=mod_mod.FIELDS,
         width=ctx.obj["width"])


@module_group.command("state")
@click.argument("name")
@json_option
@click.pass_context
@handle_errors
def module_state(ctx, name, cmd_json):
    """State of a single module."""
    emit(mod_mod.state_of(get_client(ctx), name), use_json(ctx, cmd_json),
         ctx.obj["fmt"], width=ctx.obj["width"])


@module_group.command("update-list")
@json_option
@click.pass_context
@handle_errors
def module_update_list(ctx, cmd_json):
    """Rescan the addons path, so new modules become visible."""
    client = get_client(ctx)
    if not guard_write(ctx, "Rescan the addons path on the server?"):
        raise OdooError("cancelled.")
    emit({"updated": mod_mod.update_list(client), "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


def _module_action(ctx, action, name, cmd_json, warning):
    client = get_client(ctx)
    if not guard_write(ctx, warning):
        raise OdooError("cancelled.")
    emit(mod_mod.act(client, action, name), use_json(ctx, cmd_json), ctx.obj["fmt"],
         width=ctx.obj["width"])


@module_group.command("install")
@click.argument("name")
@json_option
@click.pass_context
@handle_errors
def module_install(ctx, name, cmd_json):
    """Install a module (runs the registry update immediately)."""
    _module_action(ctx, "install", name, cmd_json,
                   f"Install '{name}'? This updates the registry on a live server.")


@module_group.command("upgrade")
@click.argument("name")
@json_option
@click.pass_context
@handle_errors
def module_upgrade(ctx, name, cmd_json):
    """Upgrade a module."""
    _module_action(ctx, "upgrade", name, cmd_json,
                   f"Upgrade '{name}'? This runs migrations on a live database.")


@module_group.command("uninstall")
@click.argument("name")
@json_option
@click.pass_context
@handle_errors
def module_uninstall(ctx, name, cmd_json):
    """Uninstall a module. Destructive: it drops the module's data."""
    _module_action(ctx, "uninstall", name, cmd_json,
                   f"UNINSTALL '{name}'? Its tables and data will be dropped.")


# ---------------------------------------------------------------------------
# db: the database service
# ---------------------------------------------------------------------------

MASTER_HELP = "Server master password (admin_passwd); or set ODOO_MASTER_PASSWORD"


def master_option(fn):
    return click.option("--master-password", envvar="ODOO_MASTER_PASSWORD",
                        help=MASTER_HELP)(fn)


@cli.group("db")
def db_group():
    """The database service: list, duplicate, drop, rename."""


@db_group.command("list")
@json_option
@click.pass_context
@handle_errors
def db_list(ctx, cmd_json):
    """Databases the server will admit to having."""
    emit(db_mod.list_databases(get_client(ctx, need_auth=False)),
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@db_group.command("exists")
@click.argument("name")
@json_option
@click.pass_context
@handle_errors
def db_exists(ctx, name, cmd_json):
    """Whether a database exists."""
    emit({"database": name, "exists": db_mod.exists(get_client(ctx, need_auth=False), name)},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@db_group.command("version")
@json_option
@click.pass_context
@handle_errors
def db_version(ctx, cmd_json):
    """Server version, without logging in."""
    emit({"server_version": db_mod.server_version(get_client(ctx, need_auth=False))},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@db_group.command("duplicate")
@click.argument("source")
@click.argument("target")
@click.option("--neutralize", is_flag=True,
              help="Disable outgoing mail, crons and payment providers in the copy")
@master_option
@json_option
@click.pass_context
@handle_errors
def db_duplicate(ctx, source, target, neutralize, master_password, cmd_json):
    """Copy a database. Use --neutralize for anything non-production."""
    client = get_client(ctx, need_auth=False)
    if not guard_write(ctx, f"Duplicate '{source}' into '{target}'?"):
        raise OdooError("cancelled.")
    db_mod.duplicate(client, master_password, source, target, neutralize)
    emit({"source": source, "target": target, "neutralized": bool(neutralize),
          "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@db_group.command("rename")
@click.argument("old")
@click.argument("new")
@master_option
@json_option
@click.pass_context
@handle_errors
def db_rename(ctx, old, new, master_password, cmd_json):
    """Rename a database."""
    client = get_client(ctx, need_auth=False)
    if not guard_write(ctx, f"Rename '{old}' to '{new}'?"):
        raise OdooError("cancelled.")
    db_mod.rename(client, master_password, old, new)
    emit({"old": old, "new": new, "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


@db_group.command("drop")
@click.argument("name")
@master_option
@json_option
@click.pass_context
@handle_errors
def db_drop(ctx, name, master_password, cmd_json):
    """Destroy a database. There is no undo."""
    client = get_client(ctx, need_auth=False)
    if not guard_write(ctx, f"DESTROY database '{name}'? This cannot be undone."):
        raise OdooError("cancelled.")
    db_mod.drop(client, master_password, name)
    emit({"dropped": name, "dry_run": bool(ctx.obj["dry_run"])},
         use_json(ctx, cmd_json), ctx.obj["fmt"], width=ctx.obj["width"])


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

@cli.command("repl")
@click.pass_context
def repl(ctx):
    """Interactive session: same commands, without re-typing the connection."""
    global _repl_mode
    from .utils.repl_skin import ReplSkin

    skin = ReplSkin("odoo", version=__version__)
    skin.print_banner()
    sess = get_session(ctx.obj.get("session_path"))
    try:
        pt_session = skin.create_prompt_session()
    except Exception:
        pt_session = None

    _repl_mode = True
    try:
        while True:
            try:
                label = sess.data.get("db") or "not connected"
                line = (skin.get_input(pt_session, project_name=label,
                                       modified=sess._modified)
                        if pt_session else input(f"odoo:{label}> "))
            except (EOFError, KeyboardInterrupt):
                break
            line = (line or "").strip()
            if not line:
                continue
            if line in ("exit", "quit", ":q"):
                break
            if line in ("help", "?"):
                click.echo(cli.get_help(ctx))
                continue
            try:
                argv = shlex.split(line)
            except ValueError as exc:
                skin.error(f"cannot parse: {exc}")
                continue
            try:
                sub = cli.main(args=argv, prog_name="", standalone_mode=False,
                               obj=ctx.obj, parent=ctx)
                del sub
            except click.ClickException as exc:
                skin.error(exc.format_message())
            except SystemExit:
                pass
            except OdooError as exc:
                skin.error(str(exc))
    finally:
        _repl_mode = False
        if sess._modified:
            try:
                sess.save_session()
            except Exception as exc:
                skin.warning(f"session not saved: {exc}")
        skin.print_goodbye()


def main(argv=None):
    return cli(args=argv, prog_name="cli-anything-odoo", standalone_mode=True)


if __name__ == "__main__":
    main()
