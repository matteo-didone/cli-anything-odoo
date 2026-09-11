"""ORM operations — the heart of the harness.

Every function takes an OdooRPC and returns plain Python, so the CLI layer
only has to render. Mutations are tagged ``writes=True`` so ``--dry-run``
can intercept them in one place.
"""

from __future__ import annotations

from ..utils.domain import build_domain, parse_ids
from ..utils.rpc import OdooError

# Methods known to be read-only, for the `call` command's confirmation logic.
READ_ONLY_METHODS = {
    "read", "search", "search_read", "search_count", "read_group", "fields_get",
    "name_search", "default_get", "check_access_rights", "get_views", "web_search_read",
}


def _kwargs(limit=None, offset=None, order=None, context=None, **extra):
    out = {}
    if limit:
        out["limit"] = limit
    if offset:
        out["offset"] = offset
    if order:
        out["order"] = order
    out.update({k: v for k, v in extra.items() if v not in (None, [], {})})
    if context:
        out["context"] = dict(context)
    return out


def search(client, model, filters=None, domain=None, limit=None, offset=None,
           order=None, context=None):
    return client.execute_kw(model, "search", [build_domain(filters, domain)],
                             _kwargs(limit, offset, order, context))


def count(client, model, filters=None, domain=None, context=None):
    return client.execute_kw(model, "search_count", [build_domain(filters, domain)],
                             _kwargs(context=context))


def search_read(client, model, filters=None, domain=None, fields=None, limit=None,
                offset=None, order=None, context=None):
    return client.execute_kw(model, "search_read", [build_domain(filters, domain)],
                             _kwargs(limit, offset, order, context, fields=fields))


def read(client, model, ids, fields=None, context=None):
    return client.execute_kw(model, "read", [parse_ids(ids)],
                             _kwargs(context=context, fields=fields))


def read_group(client, model, groupby, fields=None, filters=None, domain=None,
               limit=None, offset=None, order=None, context=None):
    if not groupby:
        raise OdooError("--groupby is required, e.g. --groupby partner_id")
    return client.execute_kw(
        model, "read_group",
        [build_domain(filters, domain), list(fields or []), list(groupby)],
        _kwargs(limit, offset, order, context, lazy=len(groupby) == 1))


def fields_get(client, model, attributes=None, like=None, context=None):
    attributes = list(attributes or
                      ["type", "string", "required", "readonly", "relation"])
    raw = client.execute_kw(model, "fields_get", [],
                            _kwargs(context=context, allfields=[],
                                    attributes=attributes)) or {}
    out = []
    for name in sorted(raw):
        if like and like.lower() not in name.lower():
            continue
        row = {"name": name}
        row.update({k: raw[name].get(k) for k in attributes})
        out.append(row)
    return out


def list_models(client, like=None, limit=None, offset=None, context=None):
    domain = [["model", "ilike", like]] if like else []
    return client.execute_kw(
        "ir.model", "search_read", [domain],
        _kwargs(limit, offset, "model asc", context,
                fields=["id", "model", "name", "transient"]))


def create(client, model, values, context=None):
    return client.execute_kw(model, "create", [values],
                             _kwargs(context=context), writes=True)


def write(client, model, ids, values, context=None):
    return client.execute_kw(model, "write", [parse_ids(ids), values],
                             _kwargs(context=context), writes=True)


def unlink(client, model, ids, context=None):
    return client.execute_kw(model, "unlink", [parse_ids(ids)],
                             _kwargs(context=context), writes=True)


def call(client, model, method, args=None, kwargs=None, context=None):
    call_kwargs = dict(kwargs or {})
    if context:
        call_kwargs["context"] = dict(context)
    return client.execute_kw(model, method, list(args or []), call_kwargs,
                             writes=method not in READ_ONLY_METHODS)
