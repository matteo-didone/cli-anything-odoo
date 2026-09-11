---
name: "cli-anything-odoo"
description: "Drive a running Odoo 18 server from the command line: query and modify any model through the ORM, inspect model metadata, install or upgrade modules, and manage databases. Use when asked to read, count, create, update or delete Odoo records (partners, sales orders, invoices, products, users), to inspect what fields a model has, to check or change module state, or to script an Odoo integration. Speaks XML-RPC to a remote or local server; Odoo itself does not need to be installed locally."
---

# cli-anything-odoo

A command-line interface to **Odoo 18's ORM**, for agents and scripts.

It talks to a *running* Odoo server over XML-RPC (`/xmlrpc/2`), exactly the way
Odoo's own external API is meant to be used. There is no local Odoo, no
database driver and no GUI involved: point it at a URL and a database and it
works, whether the server is on localhost or across the internet.

Everything is available as one-shot commands (for scripting) and inside a REPL
(for exploration), and every command takes `--json`.

## Installation

```bash
pip install cli-anything-odoo
```

**Prerequisites**

- Python 3.10+
- Network access to an Odoo 18 server, plus a login. An **API key** works in
  place of the password and is the right choice when the account has 2FA.
- Odoo does **not** need to be installed on the machine running this CLI.

## Connecting

Three sources, weakest to strongest: config file, environment, flags.

```bash
# one-off
cli-anything-odoo --url https://odoo.example.com --db mydb -u admin whoami

# or a profile in ~/.config/cli-anything-odoo/config.ini
cli-anything-odoo -p production record count res.partner
```

```ini
[production]
url = https://odoo.example.com
db = production
username = integration
password_command = security find-generic-password -w -s odoo-prod
readonly = true          ; refuses every write — recommended for production
```

The credential is never written to the session file; only the profile name is.

## Filters

Positional filters are ANDed. This is the part to learn:

| Form | Operator | Example |
|---|---|---|
| `field=value` | `=` | `state=sale` |
| `field!=value` | `!=` | `state!=draft` |
| `field~text` | `ilike` | `name~acme` |
| `field!~text` | `not ilike` | `name!~test` |
| `field>v` `field<v` `field>=v` `field<=v` | comparison | `amount_total>=1000` |
| `field:in=a,b` | `in` | `state:in=sale,done` |
| `field:not-in=a,b` | `not in` | `state:not-in=draft,cancel` |
| `field:child-of=1` | `child_of` | `categ_id:child-of=3` |
| `field:set` / `field:unset` | set / empty | `email:set` |

Values convert themselves: `42` int, `3.5` float, `true`/`false` bool, `null`
None. **A leading zero keeps the value a string** (`0431123456` stays text), so
phone numbers, postcodes and VAT codes are never corrupted.

For OR and parentheses, pass a real domain:

```bash
cli-anything-odoo record search res.partner --domain '["|",("email","!=",False),("phone","!=",False)]'
```

## Command Groups

### Cli

Drive Odoo 18 from the command line: ORM, modules and databases.

| Command | Description |
|---------|-------------|
| `version` | Server and client versions. |
| `whoami` | The authenticated user. |
| `connect` | Authenticate and remember the connection in the session. |
| `status` | What the session currently points at. |
| `reset` | Forget connection and selection. |
| `repl` | Interactive session: same commands, without re-typing the connection. |

### Model

Inspect models and fields before touching data.

| Command | Description |
|---------|-------------|
| `list` | List models (ir.model). |
| `fields` | Field definitions of a model. |

### Record

Read and change records.

| Command | Description |
|---------|-------------|
| `search` | Ids only. The result becomes the session selection, reusable as '@'. |
| `count` | How many records match. |
| `list` | search_read: the everyday command. |
| `read` | Read records by id. IDS may be '@' for the current selection. |
| `group` | read_group: totals and counts, computed by the server. |
| `create` | Create one record. |
| `write` | Update records. IDS may be '@'. |
| `delete` | Delete records permanently. IDS may be '@'. |
| `call` | Call any model method. |

### Module

Install, upgrade and inspect Odoo modules.

| Command | Description |
|---------|-------------|
| `list` | List modules. |
| `state` | State of a single module. |
| `update-list` | Rescan the addons path, so new modules become visible. |
| `install` | Install a module (runs the registry update immediately). |
| `upgrade` | Upgrade a module. |
| `uninstall` | Uninstall a module. Destructive: it drops the module's data. |

### Db

The database service: list, duplicate, drop, rename.

| Command | Description |
|---------|-------------|
| `list` | Databases the server will admit to having. |
| `exists` | Whether a database exists. |
| `version` | Server version, without logging in. |
| `duplicate` | Copy a database. Use --neutralize for anything non-production. |
| `rename` | Rename a database. |
| `drop` | Destroy a database. There is no undo. |

## Examples

### Read: the everyday commands

```bash
# how many companies
cli-anything-odoo --json record count res.partner is_company=true

# the five biggest open sale orders
cli-anything-odoo --json record list sale.order state=sale \
    --fields name,partner_id,amount_total --order "amount_total desc" --limit 5

# totals per customer, computed server-side
cli-anything-odoo --json record group sale.order \
    --groupby partner_id --fields amount_total:sum

# what fields does a model have?
cli-anything-odoo --json model fields res.partner --like mail
```

### Chaining with the selection

`search` and `list` store their result ids in the session. `@` reuses them:

```bash
cli-anything-odoo record search res.partner name~acme
cli-anything-odoo record read @ --fields name,email        # same records
cli-anything-odoo -y record write res.partner @ --set active=false
```

### Write: always guarded

```bash
# see exactly what would be sent, send nothing
cli-anything-odoo --dry-run record write res.partner 42 --set phone=0431123456

# actually do it (non-interactive requires --yes)
cli-anything-odoo -y record write res.partner 42 --set phone=0431123456

# values that must not show up in `ps`: pipe them in
printf '{"password":"%s"}' "$NEW" | \
    cli-anything-odoo -y record write res.users 41 --values-file -

# call any model method
cli-anything-odoo --json record call sale.order action_confirm --args '[[15]]' -y
```

### Modules and databases

```bash
cli-anything-odoo --json module list --state installed --like account
cli-anything-odoo -y module upgrade sale_management
cli-anything-odoo --json db list
```

## Notes for agents

- **Always pass `--json`.** Output is `{"ok": true, "result": ...}` on success
  and `{"ok": false, "error": "..."}` on failure. Exit code is 0 / 1 to match.
- **Reads are safe; writes are gated three ways.** `create`, `write`, `delete`,
  `call` (on a non read-only method), module actions and every `db` mutation
  need `--yes` when there is no TTY, are refused outright on a `readonly`
  profile, and can be rehearsed with `--dry-run`. `--dry-run` suppresses the
  write RPC itself, so it is safe against production.
- **Inspect before you mutate.** `model fields <model>` tells you the real field
  names and types; guessing them is the main cause of failed writes.
- **Odoo 18 specifics.** `name_get` is gone — read `display_name`. Datetimes are
  UTC in and out. `read_group` is still the aggregation method, and `lazy` is
  set correctly for you.
- **Module install/upgrade runs on a live server** and reloads the registry.
  Treat it as a maintenance operation, not a query.
- **Never put a secret in `--set`.** It is visible in `ps` while the call runs.
  Use `--values-file -` and pipe the JSON in. Sensitive field names are redacted
  as `***` in traces and output, so `--dry-run` on a password change is safe to
  show on screen.
- `db drop` and `module uninstall` destroy data and cannot be undone.
