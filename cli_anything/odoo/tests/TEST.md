# TEST.md — cli-anything-odoo

Written **before** the test code, per `HARNESS.md` Phase 4.

## 1. Test inventory plan

| File | Kind | Backend needed | Planned |
|---|---|---|---|
| `test_core.py` | unit, synthetic data, RPC stubbed | no | ~40 tests |
| `test_full_e2e.py` | E2E against a real Odoo 18 server | **yes** | ~15 tests |

`test_core.py` must pass on a bare checkout with no Odoo anywhere, as
CONTRIBUTING requires. `test_full_e2e.py` talks to a real server and skips
with a clear reason when there isn't one.

## 2. Unit test plan (`test_core.py`)

### `utils/domain.py` — the filter grammar
The part users touch most, and the easiest to get subtly wrong.
- Every shorthand operator maps to the right Odoo operator: `=`, `!=`, `~`→`ilike`,
  `!~`→`not ilike`, `>`, `<`, `>=`, `<=`.
- **Longest-token-first**: `a<=1` must not parse as `a < =1`. Same for `>=`, `!=`, `!~`.
- Suffix forms: `:in=`, `:not-in=`, `:child-of=`, `:set`, `:unset`.
- Value coercion: `42`→int, `3.5`→float, `true`/`false`→bool, `null`→None, rest stays str.
- A value containing `=` survives (`note=a=b`).
- Malformed filter raises `OdooError`, not a bare exception.
- `--domain` accepts both JSON (`[["a","=",1]]`) and Python (`[('a','=',1)]`).
- `build_domain` ANDs `--domain` with positional filters, in that order.
- `build_values` merges `--values` JSON with repeated `--set`; empty raises.
- `parse_ids` accepts `1,2 3`; rejects non-numeric.

### `utils/output.py` — rendering
- many2one `[7,"Acme"]` renders as `Acme (7)`; `False` renders empty.
- Truncation respects `--width`; `id` is forced into the first column.
- JSON envelope is always `{"ok": true, "result": ...}`, errors `{"ok": false, "error": ...}`.
- CSV header and row match the requested field order.

### `core/session.py` — state and locking
- A fresh session has no connection; `set_connection` marks it modified.
- `save_session` then `load_session` round-trips the data.
- **No secret is ever written**: after connecting, the session file must not
  contain the password anywhere.
- `_locked_save_json` overwrites a *larger* existing file completely — this
  catches the truncate-outside-the-lock bug the guide warns about.
- `clear()` resets selection and connection.
- Selection round-trips through `set_selection` / `get_selection`.

### `core/connection.py` — precedence
- Flags beat environment; environment beats the config file.
- `readonly = true` in a profile lands on the client as `readonly`.
- Missing url / db / username produce a readable `OdooError`, not a crash.
- `password_file` and `password_command` are honoured.
- A requested-but-absent profile raises, listing what does exist.

### `core/records.py` — call construction
With a stub client, assert the exact `(model, method, args, kwargs)` sent:
- `search`, `count`, `search_read`, `read`, `read_group` (incl. `lazy` true for
  one groupby, false for several).
- `create` / `write` / `unlink` are tagged `writes=True` so `--dry-run` catches them.
- `fields_get` filters by `--like` and keeps the requested attributes.

### `core/modules.py` / `core/database.py`
- `act()` maps install/upgrade/uninstall to the right `button_immediate_*`.
- An unknown module name raises with a hint to run `update-list`.
- Every `db` mutation without a master password raises before any network call.

### CLI layer (Click runner, RPC stubbed)
- `--json` works both globally and per-command.
- Writes without `--yes` on a non-TTY fail *and send nothing*.
- A `readonly` profile refuses writes.
- `--dry-run` prints the call and performs no write.
- `'@'` resolves to the session selection; empty selection raises.

## 3. E2E test plan (`test_full_e2e.py`)

**Invokes the real software**: a genuine Odoo 18 server (official `odoo:18.0`
image plus PostgreSQL 16), reached over XML-RPC exactly as a user would.

Set `ODOO_TEST_URL`, `ODOO_TEST_DB`, `ODOO_TEST_USER`, `ODOO_TEST_PASSWORD`.
`docker-compose.test.yml` in the repo root starts a throwaway instance.
Without those variables the whole module skips with the reason printed.

Verified against the live server:
- `common.version()` reports `server_serie` 18.0 — proof we are on the right major.
- Authentication returns a real uid; a wrong password fails cleanly.
- `res.users` read of the current uid returns the expected login.
- `search` / `search_count` agree with each other on `res.partner`.
- `fields_get` on `res.partner` includes `name` typed `char`.
- `read_group` on `res.partner` grouped by `is_company` returns counts summing
  to the total.
- Subprocess tests through the installed entry point (`_resolve_cli`), so we
  test what a user actually runs, with no hardcoded path or CWD dependency.

### Realistic workflow scenarios

**Workflow A — "read-only audit of a live database"**
*Simulates*: an agent asked "how many companies are in this Odoo, and who are
the five biggest customers?"
Operations: `connect` → `record count res.partner is_company=true` →
`record list res.partner is_company=true --fields name --limit 5 --order name` →
`record group res.partner --groupby is_company`.
Verified: counts are consistent between commands, the selection is stored in
the session file, and every output parses as JSON under `--json`.

**Workflow B — "create, amend, delete a partner"**
*Simulates*: the full write lifecycle an integration performs.
Operations: `record create res.partner --set name=...` → `record read @` →
`record write @ --set phone=...` → re-read to confirm → `record delete @` →
`record count` to confirm it is gone.
Verified: the created id comes back, the written field is actually persisted
server-side, the selection `@` chains correctly between commands, and the
database is left exactly as it was found.

**Workflow C — "the safety nets hold against a real server"**
*Simulates*: the accident this CLI must refuse to have.
Operations: the same write as B but with `--dry-run`, then with a `readonly`
profile, then non-interactively without `--yes`.
Verified: in all three cases the record count on the server is **unchanged** —
the test asserts against the live database, not against the CLI's own output.

## 4. Results

Filled in by Phase 6, below, from a real run.

### Run of 2026-09-11, against a live server

- Client: cli-anything-odoo 1.0.0, Python 3.14.6, Click 8.5.0, pytest 9.1.1
- Backend: **real Odoo 18.0** (`odoo:18.0` + `postgres:16`, via `docker-compose.test.yml`), database `e2e`
- Subprocess tests ran with `CLI_ANYTHING_FORCE_INSTALLED=1` against the installed `cli-anything-odoo` entry point

```
test_core.py::TestFilterGrammar::test_operators[name=acme-expected0] PASSED [  0%]
test_core.py::TestFilterGrammar::test_operators[id=42-expected1] PASSED [  1%]
test_core.py::TestFilterGrammar::test_operators[active=true-expected2] PASSED [  2%]
test_core.py::TestFilterGrammar::test_operators[ref=null-expected3] PASSED [  3%]
test_core.py::TestFilterGrammar::test_operators[amount=3.5-expected4] PASSED [  4%]
test_core.py::TestFilterGrammar::test_operators[name~acme-expected5] PASSED [  5%]
test_core.py::TestFilterGrammar::test_operators[name!~test-expected6] PASSED [  6%]
test_core.py::TestFilterGrammar::test_operators[state!=draft-expected7] PASSED [  7%]
test_core.py::TestFilterGrammar::test_operators[seq<5-expected8] PASSED [  8%]
test_core.py::TestFilterGrammar::test_operators[seq>5-expected9] PASSED [  9%]
test_core.py::TestFilterGrammar::test_operators[email:set-expected10] PASSED [ 10%]
test_core.py::TestFilterGrammar::test_operators[email:unset-expected11] PASSED [ 11%]
test_core.py::TestFilterGrammar::test_longest_token_wins[a<=1-expected0] PASSED [ 11%]
test_core.py::TestFilterGrammar::test_longest_token_wins[a>=1-expected1] PASSED [ 12%]
test_core.py::TestFilterGrammar::test_longest_token_wins[a!=1-expected2] PASSED [ 13%]
test_core.py::TestFilterGrammar::test_longest_token_wins[a!~x-expected3] PASSED [ 14%]
test_core.py::TestFilterGrammar::test_in_forms PASSED [ 15%]
test_core.py::TestFilterGrammar::test_value_containing_equals PASSED [ 16%]
test_core.py::TestFilterGrammar::test_malformed_raises_odoo_error PASSED [ 17%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[0431123456] PASSED [ 18%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[007] PASSED [ 19%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[00] PASSED [ 20%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[0123] PASSED [ 21%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[+0039] PASSED [ 22%]
test_core.py::TestFilterGrammar::test_leading_zero_stays_a_string[-0431] PASSED [ 22%]
test_core.py::TestFilterGrammar::test_plain_zero_and_decimals_still_convert PASSED [ 23%]
test_core.py::TestFilterGrammar::test_coercion PASSED [ 24%]
test_core.py::TestDomainAndValues::test_json_and_python_domains PASSED [ 25%]
test_core.py::TestDomainAndValues::test_domain_then_filters_in_order PASSED [ 26%]
test_core.py::TestDomainAndValues::test_domain_must_be_list PASSED [ 27%]
test_core.py::TestDomainAndValues::test_values_merge PASSED [ 28%]
test_core.py::TestDomainAndValues::test_values_empty_raises PASSED [ 29%]
test_core.py::TestDomainAndValues::test_set_without_equals_raises PASSED [ 30%]
test_core.py::TestDomainAndValues::test_parse_ids PASSED [ 31%]
test_core.py::TestDomainAndValues::test_split_list PASSED [ 32%]
test_core.py::TestRendering::test_many2one PASSED [ 33%]
test_core.py::TestRendering::test_false_is_blank PASSED [ 33%]
test_core.py::TestRendering::test_truncation PASSED [ 34%]
test_core.py::TestRendering::test_id_goes_first PASSED [ 35%]
test_core.py::TestRendering::test_csv_respects_field_order PASSED [ 36%]
test_core.py::TestRendering::test_empty_table PASSED [ 37%]
test_core.py::TestRpcHelpers::test_fault_keeps_last_useful_line PASSED [ 38%]
test_core.py::TestRpcHelpers::test_url_gets_scheme PASSED [ 39%]
test_core.py::TestRpcHelpers::test_empty_url_raises PASSED [ 40%]
test_core.py::TestSession::test_fresh_has_no_connection PASSED [ 41%]
test_core.py::TestSession::test_round_trip PASSED [ 42%]
test_core.py::TestSession::test_saving_clears_modified_flag PASSED [ 43%]
test_core.py::TestSession::test_no_secret_is_ever_persisted PASSED [ 44%]
test_core.py::TestSession::test_locked_save_truncates_a_larger_file PASSED [ 44%]
test_core.py::TestSession::test_clear PASSED     [ 45%]
test_core.py::TestSession::test_load_missing_file_is_not_an_error PASSED [ 46%]
test_core.py::TestConnectionPrecedence::test_config_is_used PASSED [ 47%]
test_core.py::TestConnectionPrecedence::test_env_beats_config PASSED [ 48%]
test_core.py::TestConnectionPrecedence::test_flag_beats_env PASSED [ 49%]
test_core.py::TestConnectionPrecedence::test_readonly_lands_on_client PASSED [ 50%]
test_core.py::TestConnectionPrecedence::test_missing_url_raises PASSED [ 51%]
test_core.py::TestConnectionPrecedence::test_unknown_profile_lists_the_real_ones PASSED [ 52%]
test_core.py::TestConnectionPrecedence::test_password_file PASSED [ 53%]
test_core.py::TestConnectionPrecedence::test_password_command PASSED [ 54%]
test_core.py::TestConnectionPrecedence::test_env_secret_beats_config PASSED [ 55%]
test_core.py::TestRecordCalls::test_search PASSED [ 55%]
test_core.py::TestRecordCalls::test_search_read_fields PASSED [ 56%]
test_core.py::TestRecordCalls::test_read_group_lazy_flag PASSED [ 57%]
test_core.py::TestRecordCalls::test_read_group_needs_groupby PASSED [ 58%]
test_core.py::TestRecordCalls::test_mutations_are_tagged_as_writes[create-extra0] PASSED [ 59%]
test_core.py::TestRecordCalls::test_mutations_are_tagged_as_writes[write-extra1] PASSED [ 60%]
test_core.py::TestRecordCalls::test_mutations_are_tagged_as_writes[unlink-extra2] PASSED [ 61%]
test_core.py::TestRecordCalls::test_reads_are_not_tagged PASSED [ 62%]
test_core.py::TestRecordCalls::test_call_marks_unknown_methods_as_writes PASSED [ 63%]
test_core.py::TestRecordCalls::test_fields_get_filters_and_keeps_attributes PASSED [ 64%]
test_core.py::TestRecordCalls::test_context_is_forwarded PASSED [ 65%]
test_core.py::TestModules::test_action_maps_to_odoo_button[install-button_immediate_install] PASSED [ 66%]
test_core.py::TestModules::test_action_maps_to_odoo_button[upgrade-button_immediate_upgrade] PASSED [ 66%]
test_core.py::TestModules::test_action_maps_to_odoo_button[uninstall-button_immediate_uninstall] PASSED [ 67%]
test_core.py::TestModules::test_unknown_action_raises PASSED [ 68%]
test_core.py::TestModules::test_missing_module_hints_at_update_list PASSED [ 69%]
test_core.py::TestModules::test_list_filters_by_state PASSED [ 70%]
test_core.py::TestDatabaseService::test_master_password_required_before_any_call[create-args0] PASSED [ 71%]
test_core.py::TestDatabaseService::test_master_password_required_before_any_call[duplicate-args1] PASSED [ 72%]
test_core.py::TestDatabaseService::test_master_password_required_before_any_call[drop-args2] PASSED [ 73%]
test_core.py::TestDatabaseService::test_master_password_required_before_any_call[rename-args3] PASSED [ 74%]
test_core.py::TestDatabaseService::test_list_needs_no_master_password PASSED [ 75%]
test_core.py::TestCliLayer::test_json_flag_global PASSED [ 76%]
test_core.py::TestCliLayer::test_json_flag_per_command PASSED [ 77%]
test_core.py::TestCliLayer::test_filters_reach_the_orm PASSED [ 77%]
test_core.py::TestCliLayer::test_write_without_yes_sends_nothing PASSED [ 78%]
test_core.py::TestCliLayer::test_write_with_yes_goes_through PASSED [ 79%]
test_core.py::TestCliLayer::test_readonly_profile_refuses PASSED [ 80%]
test_core.py::TestCliLayer::test_dry_run_performs_no_write PASSED [ 81%]
test_core.py::TestCliLayer::test_selection_chains_through_at_sign PASSED [ 82%]
test_core.py::TestCliLayer::test_empty_selection_raises PASSED [ 83%]
test_core.py::TestCliLayer::test_selection_model_mismatch_is_caught PASSED [ 84%]
test_core.py::TestCliLayer::test_error_shape_is_stable_under_json PASSED [ 85%]
test_core.py::TestCliLayer::test_session_file_is_written_after_a_search PASSED [ 86%]
test_core.py::TestCliLayer::test_dry_run_does_not_touch_the_session_file PASSED [ 87%]
test_full_e2e.py::TestLiveServer::test_server_is_odoo_18 PASSED [ 88%]
test_full_e2e.py::TestLiveServer::test_authentication_returns_a_uid PASSED [ 88%]
test_full_e2e.py::TestLiveServer::test_wrong_password_is_refused_cleanly PASSED [ 89%]
test_full_e2e.py::TestLiveServer::test_current_user_reads_back PASSED [ 90%]
test_full_e2e.py::TestLiveServer::test_search_and_count_agree PASSED [ 91%]
test_full_e2e.py::TestLiveServer::test_fields_get_describes_name PASSED [ 92%]
test_full_e2e.py::TestLiveServer::test_read_group_totals_match PASSED [ 93%]
test_full_e2e.py::TestCLISubprocess::test_version_reports_18 PASSED [ 94%]
test_full_e2e.py::TestCLISubprocess::test_whoami PASSED [ 95%]
test_full_e2e.py::TestCLISubprocess::test_audit_workflow_is_self_consistent PASSED [ 96%]
test_full_e2e.py::TestWriteLifecycle::test_full_lifecycle PASSED [ 97%]
test_full_e2e.py::TestSafetyNetsAgainstLiveServer::test_dry_run_creates_nothing PASSED [ 98%]
test_full_e2e.py::TestSafetyNetsAgainstLiveServer::test_missing_yes_creates_nothing PASSED [ 99%]
test_full_e2e.py::TestSafetyNetsAgainstLiveServer::test_readonly_profile_creates_nothing PASSED [100%]

============================= 109 passed in 2.61s ==============================
```

**============================= 109 passed in 2.61s ==============================**

### What the live layer caught that the unit layer could not

`test_full_lifecycle` failed on its first run:

```
AssertionError: assert '431123456' == '0431123456'
```

`--set phone=0431123456` was coerced to an integer, so the leading zero
never reached the database. The unit tests could not see this: they assert
that the CLI *sends* what it parsed, and it did. Only reading the value back
off a real server exposed it. Fixed in `utils/domain.py` — a leading zero
now keeps the value a string — and pinned by
`test_leading_zero_stays_a_string`, which covers phone numbers, postcodes
and VAT-style codes.

### Coverage and known gaps

| Area | Unit | E2E |
|---|---|---|
| filter grammar, coercion | yes | via workflows |
| session, locking, secret-safety | yes | session file asserted after CLI runs |
| profile/env/flag precedence | yes | readonly profile against live server |
| ORM call construction | yes | results cross-checked on the server |
| write guards | yes | **record count unchanged on the live database** |
| module install/upgrade | call mapping only | **not covered** |
| db create/drop/duplicate | guard only | **not covered** |

The two gaps are deliberate. Installing or upgrading a module reloads the
registry, and `db drop` destroys a database; both take minutes and leave
the fixture in a different state than they found it. They are exercised by
hand against a throwaway instance. If the maintainers want them automated,
the natural shape is a separate, slower suite with its own disposable
container per test.
