"""Turning friendly command-line filters into Odoo domains."""

from __future__ import annotations

import ast
import json
import re

from .rpc import OdooError

# Longest tokens first, or '<' swallows '<='.
SHORTHAND_OPS = [
    ("!~", "not ilike"), ("!=", "!="), (">=", ">="), ("<=", "<="),
    ("~", "ilike"), (">", ">"), ("<", "<"), ("=", "="),
]

SUFFIX_OPS = {
    ":in": "in", ":not-in": "not in", ":child-of": "child_of",
    ":parent-of": "parent_of", ":like": "like", ":ilike": "ilike",
}


# A leading zero means the value is an identifier, not a number: phone numbers,
# postcodes, VAT codes and product references must survive intact.
_LEADING_ZERO = re.compile(r"[+-]?0\d+$")


def coerce(text):
    """'42' -> 42, 'true' -> True, 'null' -> None; anything else stays a string.

    '0431123456' stays a string: turning it into 431123456 would silently
    corrupt the record.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in ("true", "vero"):
        return True
    if lowered in ("false", "falso"):
        return False
    if lowered in ("null", "none"):
        return None
    if _LEADING_ZERO.match(stripped):
        return text
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def parse_literal(text, what="value"):
    """JSON first, then a Python literal, so both [["a","=",1]] and [('a','=',1)] work."""
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(text)
        except (ValueError, SyntaxError, TypeError):
            continue
    raise OdooError(f"cannot parse {what}: {text!r}")


def parse_filter(token):
    """'name~acme' -> ('name', 'ilike', 'acme')."""
    if token.endswith(":set"):
        return (token[:-4], "!=", False)
    if token.endswith(":unset"):
        return (token[:-6], "=", False)

    for suffix, op in SUFFIX_OPS.items():
        marker = suffix + "="
        if marker in token:
            field, _, raw = token.partition(marker)
            if not field:
                break
            if op in ("in", "not in"):
                return (field, op, [coerce(v) for v in raw.split(",") if v != ""])
            return (field, op, coerce(raw))

    for token_op, op in SHORTHAND_OPS:
        idx = token.find(token_op)
        if idx > 0:
            return (token[:idx], op, coerce(token[idx + len(token_op):]))

    raise OdooError(
        f"unrecognized filter: {token!r}. Expected 'field=value', 'field~text', "
        "'field:in=1,2', 'field:set', or a full --domain.")


def build_domain(filters=None, domain=None):
    """--domain and positional filters, ANDed together."""
    result = []
    if domain:
        parsed = parse_literal(domain, "domain")
        if not isinstance(parsed, list):
            raise OdooError('domain must be a list, e.g. [("name","=","x")]')
        result.extend(list(item) if isinstance(item, tuple) else item for item in parsed)
    for token in filters or []:
        result.append(list(parse_filter(token)))
    return result


def read_values_file(path):
    """Read a JSON object of values from a file, or from stdin when path is '-'.

    Keeps secrets out of argv: anything passed as --set lands in the process
    command line and is visible to `ps` for as long as the call runs.
    """
    import sys
    if path == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise OdooError(f"cannot read values file: {exc}") from exc
    if not raw.strip():
        raise OdooError("no values on stdin." if path == "-" else f"{path} is empty.")
    parsed = parse_literal(raw.strip(), "values")
    if not isinstance(parsed, dict):
        raise OdooError('the values file must hold an object, e.g. {"name": "Acme"}')
    return parsed


def build_values(values=None, pairs=None, values_file=None):
    """--values JSON, --values-file/stdin, and repeated --set, merged in that order."""
    out = {}
    if values_file:
        out.update(read_values_file(values_file))
    if values:
        parsed = parse_literal(values, "values")
        if not isinstance(parsed, dict):
            raise OdooError('--values must be an object, e.g. \'{"name": "Acme"}\'')
        out.update(parsed)
    for token in pairs or []:
        field, sep, raw = token.partition("=")
        if not sep or not field:
            raise OdooError(f"--set expects field=value, got {token!r}")
        out[field] = coerce(raw)
    if not out:
        raise OdooError("no values given: use --values '{...}' or --set field=value.")
    return out


def parse_ids(text):
    """'1,2 3' -> [1, 2, 3]."""
    ids = []
    for chunk in str(text).replace(" ", ",").split(","):
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            raise OdooError(f"not a numeric id: {chunk!r}") from None
    if not ids:
        raise OdooError("no ids given.")
    return ids


def split_list(values):
    """['a,b', 'c'] -> ['a', 'b', 'c']."""
    out = []
    for value in values or []:
        out.extend(part.strip() for part in str(value).split(",") if part.strip())
    return out
