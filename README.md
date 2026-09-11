# cli-anything-odoo

**Agent-native CLI for Odoo 18.** Query and change any model through the ORM,
inspect metadata, manage modules and databases — from the command line, with
`--json` everywhere.

Built to the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness
standard.

```bash
cli-anything-odoo --json record list sale.order state=sale \
    --fields name,partner_id,amount_total --order "amount_total desc" --limit 5
```

## Why

Odoo already has an excellent external API, and `odoo-bin shell` for people with
shell access to the server. Neither helps an agent that just needs to ask a
*running* instance a question. This CLI is that missing piece: no source
checkout, no database credentials, no GUI — a URL, a database and a login.

## Install

```bash
pip install cli-anything-odoo
```

Python 3.10+. Odoo does **not** need to be installed locally.

## Connect

Config file → environment → flags, each beating the one before.

```ini
# ~/.config/cli-anything-odoo/config.ini
[production]
url = https://odoo.example.com
db = production
username = integration
password_command = security find-generic-password -w -s odoo-prod
readonly = true
```

```bash
cli-anything-odoo -p production whoami
cli-anything-odoo --url https://odoo.example.com --db mydb -u admin record count res.partner
```

An **API key** works wherever a password does, and is the right answer for
accounts with 2FA. The session file stores only the profile name — never the
secret.

## Commands

| Group | Commands |
|---|---|
| root | `version` `whoami` `connect` `status` `reset` `repl` |
| `model` | `list` `fields` |
| `record` | `search` `count` `list` `read` `group` `create` `write` `delete` `call` |
| `module` | `list` `state` `update-list` `install` `upgrade` `uninstall` |
| `db` | `list` `exists` `version` `duplicate` `rename` `drop` |

Run with no arguments for the REPL.

## Filters

| Form | Operator | Example |
|---|---|---|
| `field=value` | `=` | `state=sale` |
| `field!=value` | `!=` | `state!=draft` |
| `field~text` | `ilike` | `name~acme` |
| `field!~text` | `not ilike` | `name!~test` |
| `field>v` `<v` `>=v` `<=v` | comparison | `amount_total>=1000` |
| `field:in=a,b` | `in` | `state:in=sale,done` |
| `field:not-in=a,b` | `not in` | `state:not-in=draft,cancel` |
| `field:child-of=1` | `child_of` | `categ_id:child-of=3` |
| `field:set` / `field:unset` | set / empty | `email:set` |

Values convert themselves — except that **a leading zero keeps the value a
string**, so `0431123456` reaches Odoo intact.

Anything needing OR or parentheses takes a real domain:

```bash
cli-anything-odoo record search res.partner --domain '["|",("email","!=",False),("phone","!=",False)]'
```

## Chaining

`search` and `list` remember the ids they found; `@` reuses them.

```bash
cli-anything-odoo record search res.partner name~acme
cli-anything-odoo record read @ --fields name,email
cli-anything-odoo -y record write res.partner @ --set active=false
```

## Writes have three nets

Odoo has **no undo**. So:

1. `readonly = true` on a profile refuses every write.
2. `--dry-run` shows the exact RPC and sends nothing.
3. Confirmation is required, and non-interactive runs **fail closed** — no TTY
   and no `--yes` means the command errors rather than proceeding.

```bash
cli-anything-odoo --dry-run record write res.partner 42 --set phone=0431123456
cli-anything-odoo -y       record write res.partner 42 --set phone=0431123456
```

### Values that must not appear in `ps`

Anything passed with `--set` lands in the process command line, where other
users on the machine can read it for as long as the call runs. For secrets,
pipe the values in instead:

```bash
printf '{"password":"%s"}' "$NEW" | \
  cli-anything-odoo -y record write res.users 41 --values-file -
```

Sensitive field names — `password`, `api_key`, `token`, `secret` and friends —
are redacted as `***` in `--verbose` and `--dry-run` traces and in the command's
own output, so rehearsing a password change does not print the password.

## For agents

`--json` gives `{"ok": true, "result": ...}` or `{"ok": false, "error": "..."}`,
with exit code 0 / 1 to match. See
[`SKILL.md`](skills/cli-anything-odoo/SKILL.md).

## Tests

```bash
pip install -e ".[dev]"
pytest cli_anything/odoo/tests/test_core.py -v          # no server needed
```

End-to-end against a real Odoo 18:

```bash
docker compose -f docker-compose.test.yml up -d
export ODOO_TEST_URL=http://localhost:18169 ODOO_TEST_DB=e2e \
       ODOO_TEST_USER=admin ODOO_TEST_PASSWORD=admin
export CLI_ANYTHING_FORCE_INSTALLED=1
pytest cli_anything/odoo/tests/ -v
docker compose -f docker-compose.test.yml down -v
```

Results and the test plan: [`TEST.md`](cli_anything/odoo/tests/TEST.md).
Architecture and design decisions: [`ODOO.md`](ODOO.md).

## License

MIT
