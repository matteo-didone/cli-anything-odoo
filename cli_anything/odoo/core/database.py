"""The `db` RPC service (``odoo/service/db.py``).

These calls need the *master password* (``admin_passwd``), not a user login,
and many are refused unless ``list_db``/db management is enabled on the server.
"""

from __future__ import annotations

from ..utils.rpc import OdooError


def list_databases(client):
    return client.db_call("list")


def exists(client, name):
    return bool(client.db_call("db_exist", name))


def server_version(client):
    return client.db_call("server_version")


def create(client, master_password, name, demo=False, lang="en_US", admin_password="admin"):
    _require_master(master_password)
    return client.db_call("create_database", master_password, name, bool(demo),
                          lang, admin_password, writes=True)


def duplicate(client, master_password, source, target, neutralize=False):
    _require_master(master_password)
    return client.db_call("duplicate_database", master_password, source, target,
                          bool(neutralize), writes=True)


def drop(client, master_password, name):
    _require_master(master_password)
    return client.db_call("drop", master_password, name, writes=True)


def rename(client, master_password, old, new):
    _require_master(master_password)
    return client.db_call("rename", master_password, old, new, writes=True)


def _require_master(master_password):
    if not master_password:
        raise OdooError(
            "this operation needs the server master password (admin_passwd). "
            "Pass --master-password or set ODOO_MASTER_PASSWORD.")
