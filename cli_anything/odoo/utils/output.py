"""Rendering: JSON for agents, tables for humans, CSV for spreadsheets."""

from __future__ import annotations

import csv
import io
import json

import click


# Field names whose values must never be echoed back to a terminal or a log.
SENSITIVE = ("password", "passwd", "api_key", "apikey", "token", "secret",
             "private_key", "otp")


def _is_sensitive(name):
    return any(marker in str(name).lower() for marker in SENSITIVE)


def redact(value):
    """Replace sensitive values in anything about to be printed.

    The --verbose / --dry-run trace prints the RPC call verbatim; without this,
    rehearsing a password change would put the new password on screen.
    """
    if isinstance(value, dict):
        return {k: ("***" if _is_sensitive(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def cell(value, width=0):
    if value is None or value is False:
        text = ""
    elif isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], int):
        text = f"{value[1]} ({value[0]})"              # many2one [id, name]
    elif isinstance(value, (list, tuple)):
        text = ", ".join(str(v) for v in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("\t", " ").strip()
    if width and len(text) > width:
        text = text[: max(1, width - 1)] + "…"
    return text


def columns_for(records, requested=None):
    if requested:
        return list(requested)
    seen = []
    for record in records:
        for key in record:
            if key not in seen:
                seen.append(key)
    if "id" in seen:
        seen.insert(0, seen.pop(seen.index("id")))
    return seen


def render_table(records, cols, width=40):
    if not records:
        return "(no records)"
    rows = [[cell(r.get(c), width) for c in cols] for r in records]
    widths = [len(c) for c in cols]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    out = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)).rstrip(),
           "  ".join("-" * w for w in widths)]
    out += ["  ".join(v.ljust(widths[i]) for i, v in enumerate(row)).rstrip() for row in rows]
    out.append(f"\n{len(records)} record(s)")
    return "\n".join(out)


def render_csv(records, cols):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({c: cell(record.get(c)) for c in cols})
    return buf.getvalue().rstrip("\r\n")


def emit(result, use_json=False, fmt="table", cols=None, width=40, meta=None):
    """Single output door. --json always wins, so agents get a stable shape."""
    if use_json:
        payload = {"ok": True, "result": result}
        if meta:
            payload.update(meta)
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    if isinstance(result, list) and result and isinstance(result[0], dict):
        columns = columns_for(result, cols)
        click.echo(render_csv(result, columns) if fmt == "csv"
                   else render_table(result, columns, width))
        return
    if isinstance(result, list):
        for item in result:
            click.echo(item)
        return
    if isinstance(result, dict):
        click.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    if result is not None:
        click.echo(result)


def emit_error(message, use_json=False):
    if use_json:
        click.echo(json.dumps({"ok": False, "error": str(message)}, ensure_ascii=False))
    else:
        click.echo(f"error: {message}", err=True)
