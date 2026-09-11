# ODOO.md — harness SOP

How this harness is put together, and why. Follows `HARNESS.md`.

## Phase 1 — Codebase analysis

Read against a real checkout of `odoo/odoo` at tag `18.0`
(commit `2fe4af4b`, 2026-09-10).

**The backend engine is Odoo's own RPC layer**, not a separate library. Three
services are dispatched from `odoo/addons/base/controllers/rpc.py`:

| Service | Source | What it gives us |
|---|---|---|
| `common` | `odoo/service/common.py` | `authenticate`, `version`, `about` |
| `object` | `odoo/service/model.py` | `execute_kw` — the entire ORM |
| `db` | `odoo/service/db.py` | `list`, `create_database`, `duplicate_database`, `drop`, `dump`, `restore`, `rename`, `server_version` |

Endpoints: `/xmlrpc/2/<service>` (int fault codes — the modern one, what we
use), `/xmlrpc/<service>` (legacy, string fault codes), and `/jsonrpc`.

**There is no GUI to reverse-engineer.** This is the key difference from a
Blender- or GIMP-style harness: Odoo's web client is itself a client of this
same API, so "mapping GUI actions to API calls" is already done by Odoo. The
harness's job is ergonomics and safety, not translation.

**Data model.** PostgreSQL underneath, but never touched directly — the ORM is
the only correct door (it enforces access rules, computed fields, constraints
and business logic). Model metadata is itself queryable: `ir.model`,
`ir.model.fields`, and `fields_get` on any model.

**Existing CLI tools.** `odoo-bin` (`odoo/cli/`) ships `server`, `shell`, `db`,
`scaffold`, `populate`, `neutralize`, `cloc`, `upgrade_code` and others. They
all require the source, the addons path and database credentials — they are
*server-side* tools. None of them lets an agent query a running instance from
elsewhere. That is the gap this harness fills.

**Commands / undo.** Odoo has no undo stack. Mutations are immediate and
irreversible. That single fact drives the whole safety design below.

## Phase 2 — Architecture

**Interaction model: both.** Subcommands for scripting, REPL for exploration.
The REPL is the default when no subcommand is given (`invoke_without_command`).

**Command groups**, matching Odoo's own domains:

| Group | Domain | Backed by |
|---|---|---|
| *(root)* | connection & session | `common` |
| `model` | metadata | `ir.model`, `fields_get` |
| `record` | the ORM | `object.execute_kw` |
| `module` | lifecycle | `ir.module.module` buttons |
| `db` | databases | `db` service |

**State model.** The session holds *where we are pointed* and *what we last
selected* — not a document. Stored as JSON at
`~/.config/cli-anything-odoo/session.json`, written through `_locked_save_json`
(open `r+`, lock, truncate inside the lock).

The selection is what makes chaining work: `record search` stores the ids it
found, and any later command accepts `@` in their place.

> **A deliberate rule: the session file never contains a secret.** It stores the
> profile *name*; the password or API key is resolved from config, environment
> or keychain on every run. A session file can therefore be copied, inspected or
> committed by mistake without leaking anything. A unit test asserts this.

**Output.** `--json` on the root group *and* on every command, always shaped
`{"ok": bool, ...}`. Tables and CSV for humans.

## Phase 3 — Implementation notes

```
cli_anything/odoo/
├── odoo_cli.py        Click layer: groups, guards, REPL
├── core/
│   ├── connection.py  profile/env/flag precedence, secret resolution
│   ├── session.py     state + _locked_save_json
│   ├── records.py     the ORM
│   ├── modules.py     ir.module.module
│   └── database.py    the db service
└── utils/
    ├── rpc.py         the only module that touches the network
    ├── domain.py      filter grammar
    ├── output.py      rendering
    └── repl_skin.py   copied verbatim from the plugin
```

**Three nets before any write**, because Odoo has no undo:

1. `readonly = true` on a profile refuses every mutation outright.
2. `--dry-run` prints the exact RPC and suppresses the call. The suppression
   lives in one place — `OdooRPC.execute_kw(..., writes=True)` — so a new
   mutating command cannot forget it, as long as it is tagged. Unit tests assert
   the tagging of every mutation.
3. Confirmation is required, and **non-interactive runs fail closed**: without a
   TTY and without `--yes`, the command errors instead of proceeding.

**Auto-save.** `HARNESS.md`'s auto-save clause is written for file-backed
projects; Odoo is a service wrapper, so there is no document to lose. The
pattern is applied to what *is* persistent here — the session — via
`@cli.result_callback()`, and `--dry-run` suppresses both the write RPC and the
session write. The REPL saves on exit instead, as the guide specifies.

## Phase 6 — What the live tests caught

The E2E suite runs against a real Odoo 18 (`docker-compose.test.yml`). It
immediately earned its keep: `--set phone=0431123456` was reaching the server as
the integer `431123456`. The unit tests could not see it — they only checked
that the CLI sent what it parsed — while the E2E read the value back from the
database and found it changed. Leading zeros now keep a value as a string, with
a regression test naming the case.

That is the argument for the E2E layer in one example: it is the only place
where "what we sent" is checked against "what Odoo stored".

## Deviations from HARNESS.md, and why

- **No `utils/<software>_backend.py`.** That module exists to locate and shell
  out to a local binary. Odoo is reached over the network, so `utils/rpc.py`
  takes its place. Nothing is executed locally.
- **No project/export commands.** There is no document to open or render; the
  database *is* the state, and it lives on the server.
- **`--dry-run` suppresses writes rather than a save.** Adapted, as above, to a
  system with no local document and no undo.
