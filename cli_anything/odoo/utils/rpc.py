"""XML-RPC backend for Odoo 18.

Odoo exposes three RPC services at ``/xmlrpc/2/<service>``
(``odoo/addons/base/controllers/rpc.py``):

* ``common`` — ``authenticate``, ``version``, ``about``
* ``object`` — ``execute_kw``: the whole ORM
* ``db``     — ``list``, ``create_database``, ``duplicate_database``, ``drop``,
               ``dump``, ``restore``, ``rename``, ``server_version``

This module is the only place that talks to the network.
"""

from __future__ import annotations

import ssl
import urllib.parse
import xmlrpc.client

DEFAULT_TIMEOUT = 120.0


class OdooError(Exception):
    """An expected failure: shown as a message, never as a traceback."""


class _TimeoutMixin:
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout


class TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    def __init__(self, timeout, context=None):
        super().__init__(context=context)
        self._timeout = timeout


def clean_fault(fault: xmlrpc.client.Fault) -> str:
    """Odoo sends back a full traceback; keep the last meaningful line."""
    text = (fault.faultString or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return f"server error (code {fault.faultCode})"
    for line in reversed(lines):
        if not line.startswith(('File "', "Traceback", "  ")):
            return line
    return lines[-1]


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise OdooError("no server URL given")
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    return url


class OdooRPC:
    """Thin, lazy client. Nothing is sent until a call is made."""

    def __init__(self, url, db=None, username=None, password=None,
                 timeout=DEFAULT_TIMEOUT, insecure=False, dry_run=False,
                 on_call=None):
        self.url = normalize_url(url)
        self.db = db
        self.username = username
        self.password = password
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.insecure = bool(insecure)
        self.dry_run = bool(dry_run)
        self.on_call = on_call            # hook for --verbose / --dry-run tracing
        self._uid = None
        self._proxies = {}

    # -- plumbing -----------------------------------------------------------

    def _transport(self):
        if urllib.parse.urlparse(self.url).scheme == "https":
            ctx = ssl._create_unverified_context() if self.insecure else None
            return TimeoutSafeTransport(self.timeout, context=ctx)
        return TimeoutTransport(self.timeout)

    def proxy(self, service):
        if service not in self._proxies:
            self._proxies[service] = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/{service}",
                transport=self._transport(), allow_none=True)
        return self._proxies[service]

    def _guard(self, fn, what):
        try:
            return fn()
        except xmlrpc.client.Fault as exc:
            raise OdooError(clean_fault(exc)) from exc
        except OSError as exc:
            raise OdooError(f"{what} failed ({self.url}): {exc}") from exc

    # -- common -------------------------------------------------------------

    def version(self):
        return self._guard(lambda: self.proxy("common").version(), "version")

    @property
    def uid(self):
        if self._uid is None:
            for name, value in (("database", self.db), ("username", self.username),
                                ("password", self.password)):
                if not value:
                    raise OdooError(f"cannot authenticate: no {name} set")
            uid = self._guard(
                lambda: self.proxy("common").authenticate(
                    self.db, self.username, self.password, {}),
                "authentication")
            if not uid:
                raise OdooError(
                    f"authentication refused for '{self.username}' on database "
                    f"'{self.db}'. Check database, login and password/API key.")
            self._uid = uid
        return self._uid

    # -- object (the ORM) ---------------------------------------------------

    def execute_kw(self, model, method, args=None, kwargs=None, writes=False):
        args, kwargs = list(args or []), dict(kwargs or {})
        if self.on_call:
            self.on_call(model, method, args, kwargs)
        if self.dry_run and writes:
            return None
        uid = self.uid
        return self._guard(
            lambda: self.proxy("object").execute_kw(
                self.db, uid, self.password, model, method, args, kwargs),
            f"{model}.{method}")

    # -- db service ---------------------------------------------------------

    def db_call(self, method, *args, writes=False):
        if self.on_call:
            self.on_call("db", method, list(args), {})
        if self.dry_run and writes:
            return None
        return self._guard(
            lambda: getattr(self.proxy("db"), method)(*args), f"db.{method}")
