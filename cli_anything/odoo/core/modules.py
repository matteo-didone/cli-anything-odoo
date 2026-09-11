"""Module management, through ir.module.module.

Odoo's own buttons are the supported path (``odoo/addons/base/models/
ir_module.py``): ``button_immediate_install`` / ``_upgrade`` / ``_uninstall``
run the registry update in the same request.
"""

from __future__ import annotations

from ..utils.rpc import OdooError

FIELDS = ["id", "name", "shortdesc", "state", "latest_version", "author", "application"]

_ACTIONS = {
    "install": "button_immediate_install",
    "upgrade": "button_immediate_upgrade",
    "uninstall": "button_immediate_uninstall",
}


def list_modules(client, like=None, state=None, limit=None, context=None):
    domain = []
    if like:
        domain.append(["name", "ilike", like])
    if state:
        domain.append(["state", "=", state])
    kwargs = {"fields": FIELDS, "order": "name asc"}
    if limit:
        kwargs["limit"] = limit
    if context:
        kwargs["context"] = dict(context)
    return client.execute_kw("ir.module.module", "search_read", [domain], kwargs)


def find(client, name):
    ids = client.execute_kw("ir.module.module", "search",
                            [[["name", "=", name]]], {"limit": 1})
    if not ids:
        raise OdooError(f"module '{name}' not found. Try 'module update-list' first.")
    return ids[0]


def state_of(client, name):
    rec = client.execute_kw("ir.module.module", "read",
                            [[find(client, name)], ["name", "state", "latest_version"]])
    return rec[0] if rec else None


def update_list(client):
    """Rescan the addons path — needed before installing a freshly added module."""
    return client.execute_kw("ir.module.module", "update_list", [], writes=True)


def act(client, action, name):
    if action not in _ACTIONS:
        raise OdooError(f"unknown action '{action}'. One of: {', '.join(_ACTIONS)}")
    module_id = find(client, name)
    result = client.execute_kw("ir.module.module", _ACTIONS[action],
                               [[module_id]], writes=True)
    if client.dry_run:
        return {"module": name, "action": action, "dry_run": True}
    return {"module": name, "action": action, "result": result,
            "state": (state_of(client, name) or {}).get("state")}
