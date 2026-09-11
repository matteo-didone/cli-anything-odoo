"""Resolving *where to connect and as whom*, from profiles, env and flags.

Precedence, weakest to strongest: config file -> environment -> command line.
"""

from __future__ import annotations

import configparser
import getpass
import os
import shlex
import subprocess
import sys

from ..utils.rpc import OdooError, OdooRPC

DEFAULT_CONFIG = os.path.expanduser("~/.config/cli-anything-odoo/config.ini")


def _as_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def read_profile(name=None, config_path=None):
    """Return the raw [profile] section, or {} when there is no config file."""
    path = config_path or os.environ.get("ODOO_CONFIG") or DEFAULT_CONFIG
    profile = name or os.environ.get("ODOO_PROFILE") or "default"
    if not os.path.exists(path):
        if name:
            raise OdooError(f"no config file at {path}, but profile '{name}' was requested")
        return {}, profile
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise OdooError(f"unreadable config ({path}): {exc}") from exc
    if parser.has_section(profile):
        return dict(parser.items(profile)), profile
    if name:
        available = ", ".join(parser.sections()) or "none"
        raise OdooError(f"profile '{profile}' not in {path}. Available: {available}")
    return {}, profile


def resolve_secret(section, explicit=None, username=None, prompt=True):
    """Password or API key, from the most explicit source to the least."""
    if explicit:
        return explicit
    for var in ("ODOO_PASSWORD", "ODOO_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    if section.get("password"):
        return section["password"]
    if section.get("password_file"):
        path = os.path.expanduser(section["password_file"])
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            raise OdooError(f"unreadable password_file: {exc}") from exc
    if section.get("password_command"):
        try:
            done = subprocess.run(shlex.split(section["password_command"]),
                                  capture_output=True, text=True, check=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OdooError(f"password_command failed: {exc}") from exc
        return done.stdout.strip()
    if prompt and sys.stdin.isatty():
        return getpass.getpass(f"Password or API key for {username or 'user'}: ")
    raise OdooError(
        "no credential found. Use --password, ODOO_API_KEY, or password_file / "
        "password_command in the profile.")


def build_client(profile=None, config=None, url=None, db=None, username=None,
                 password=None, timeout=None, insecure=False, dry_run=False,
                 on_call=None, need_auth=True, session=None):
    """Assemble an OdooRPC from every configuration source."""
    section, profile_name = read_profile(profile, config)

    url = url or os.environ.get("ODOO_URL") or section.get("url")
    db = db or os.environ.get("ODOO_DB") or section.get("db")
    username = (username or os.environ.get("ODOO_USER")
                or os.environ.get("ODOO_USERNAME") or section.get("username"))

    # Fall back to whatever the stored session was pointed at.
    if session is not None and session.has_connection():
        url = url or session.data.get("url")
        db = db or session.data.get("db")
        username = username or session.data.get("username")

    if not url:
        raise OdooError(
            "no server URL. Pass --url, set ODOO_URL, or define a profile "
            f"in {config or DEFAULT_CONFIG}.")

    secret = None
    if need_auth:
        missing = [n for n, v in (("database", db), ("username", username)) if not v]
        if missing:
            raise OdooError(f"missing {', '.join(missing)}: pass --db / --user or use a profile.")
        secret = resolve_secret(section, password, username)

    client = OdooRPC(
        url=url, db=db, username=username, password=secret,
        timeout=timeout or section.get("timeout") or None,
        insecure=insecure or _as_bool(section.get("insecure")),
        dry_run=dry_run, on_call=on_call)
    client.profile_name = profile_name
    client.readonly = _as_bool(section.get("readonly"))
    return client
