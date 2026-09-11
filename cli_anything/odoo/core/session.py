"""Session state: which server we are pointed at, and what we last selected.

The session file never holds a password. It stores the *profile name*; the
secret is resolved from the config file, the environment or the keychain on
every run. That way an agent can share a session file without leaking
credentials.
"""

from __future__ import annotations

import json
import os
import time

DEFAULT_SESSION_PATH = os.path.expanduser("~/.config/cli-anything-odoo/session.json")


def _locked_save_json(path, data, **dump_kwargs) -> None:
    """Atomically write JSON with exclusive file locking.

    Never ``open("w")`` first: that truncates before any lock can be taken.
    """
    try:
        f = open(path, "r+")                    # no truncation on open
    except FileNotFoundError:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        f = open(path, "w")                     # first save — file is absent
    with f:
        locked = False
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError):
            pass                                # Windows / unsupported FS
        try:
            f.seek(0)
            f.truncate()                        # truncate INSIDE the lock
            json.dump(data, f, **dump_kwargs)
            f.flush()
        finally:
            if locked:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Session:
    """In-memory state, optionally backed by a JSON file."""

    def __init__(self, path=None):
        self.path = path or DEFAULT_SESSION_PATH
        self._modified = False
        self.data = {
            "profile": None,        # profile name; secrets are never stored
            "url": None,
            "db": None,
            "username": None,
            "uid": None,
            "context": {},
            "selection": {"model": None, "ids": []},
            "updated_at": None,
        }

    # -- state --------------------------------------------------------------

    def has_connection(self):
        return bool(self.data.get("url") and self.data.get("db"))

    def snapshot(self):
        """Copy taken before a mutation, so callers can diff or roll back."""
        return json.loads(json.dumps(self.data))

    def set_connection(self, profile=None, url=None, db=None, username=None, uid=None):
        for key, value in (("profile", profile), ("url", url), ("db", db),
                           ("username", username), ("uid", uid)):
            if value is not None:
                self.data[key] = value
        self._modified = True

    def set_selection(self, model, ids):
        self.data["selection"] = {"model": model, "ids": list(ids or [])}
        self._modified = True

    def get_selection(self):
        sel = self.data.get("selection") or {}
        return sel.get("model"), list(sel.get("ids") or [])

    def set_context(self, context):
        self.data["context"] = dict(context or {})
        self._modified = True

    def clear(self):
        model = self.data.get("selection", {}).get("model")
        self.__init__(self.path)
        self._modified = True
        return model

    # -- persistence --------------------------------------------------------

    def load_session(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
        except (FileNotFoundError, ValueError):
            return False
        if isinstance(stored, dict):
            self.data.update(stored)
            self._modified = False
            return True
        return False

    def save_session(self):
        self.data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _locked_save_json(self.path, self.data, indent=2, ensure_ascii=False)
        self._modified = False
        return self.path


_SESSION = None


def get_session(path=None):
    global _SESSION
    if _SESSION is None or (path and path != _SESSION.path):
        _SESSION = Session(path)
        _SESSION.load_session()
    return _SESSION


def reset_session():
    """Test hook: drop the singleton."""
    global _SESSION
    _SESSION = None
