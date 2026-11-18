# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django database backend for IBM Db2 for i (iSeries/AS400) over **pyodbc + the IBM i Access ODBC driver**. Forked from IBM's `python-ibmdb-django` (which used `ibm_db_dbi`), so some comments still say "DB2"; the driver is pyodbc.

- Importable package: `src/django_iseries` (`'ENGINE': 'django_iseries'`). Distribution: `django-pyodbc-iseries`.
- Supported: Django 5.2 LTS, 6.0, 6.1; Python 3.10+; IBM i 7.3+ (developed against 7.5).
- User-facing docs (settings, OPTIONS, limitations) live in `README.md`; keep them in sync with behavior changes, and add a line to `CHANGES`.

## Commands

```sh
pip install -e ".[test]"
pytest                                              # pytest.ini: tests.settings, --reuse-db --nomigrations, env_files=.env, pythonpath=src
pytest tests/test_cursor_wrapper.py                 # offline: cursor wrapper (placeholders, literals, error mapping)
pytest tests/test_connection.py                     # offline: connection string building
pytest tests/test_django_compat.py                  # offline: SQL compilation against current Django
pytest tests/test_live_orm.py::test_bulk_update     # single live test
```

- Live tests (`django_db` marker) need an IBM i system configured in `.env` (copy `.env.example`); `tests/conftest.py` skips them when `TEST_SYSTEM_HOST` is unset. The offline files run anywhere.
- **Run targeted tests for a change**; the full live suite takes minutes and hits the shared test library.
- `tests/settings.py` runs the suite in the `.env` library via `TEST = {'SCHEMA': ..., 'CREATE_DB': False}` (see `creation.py`: tests normally get their own `TEST_<CURRENTSCHEMA>` schema). `tests/test_live_test_schema.py` exercises create/drop with throwaway `DJT*` schemas.
- The test library must be dedicated and **journaled** (unjournaled tables fail with SQL7008 under commitment control). Tests create tables there; `--reuse-db` keeps them between runs, so a model/schema change in `tests/models.py` may need the old test tables dropped.
- Test against several Django versions with separate virtualenvs (`pip install "Django~=5.2.0"` etc.); CI (`.github/workflows/tests.yml`) runs the offline suite on 5.2/6.0/6.1.

## Architecture

Standard Django backend layout; `base.DatabaseWrapper` wires the pieces and `base.DatabaseFeatures` holds the capability flags. Most behavior comes from Django's base classes; the modules override only Db2 for i specifics.

### Connection and cursor (`pybase.py`)

- `DatabaseWrapper.get_new_connection` builds the ODBC connection string from settings + `OPTIONS` (`CONNECTION_STRING_KEYWORDS`, `dbq` library list, `driver`, `maxfieldlen`); recognized pyodbc kwargs go to `pyodbc.connect`, any other option becomes `KEY=value`. Values are ODBC-quoted. After connecting it sets `CURRENT_SCHEMA` and the job's reply list (`message_replies`).
- `CONNECTION_DEFAULTS`: `CMT=1` (*CS) + `CONCURRENTACCESSRESOLUTION=1` (currently committed). Autocommit statements run without commitment control unless `TRUEAUTOCOMMIT=1` (live-verified, `tests/test_live_isolation.py`).
- `MAXFIELDLEN` defaults to the driver maximum because pyodbc hangs fetching BLOBs through LOB locators.
- `DB2CursorWrapper` wraps (cannot subclass) `pyodbc.Cursor`:
  - Converts `%s` to `?`; a literal `%s` must be written `%%s`.
  - Inlines parameters in the `SELECT` list as literals via `sql_literal()` (Db2 for i rejects untyped `?` there, SQL0418). `SQL_SCAN_REGEX` finds markers outside string literals, quoted identifiers and comments (it replaced `sqlparse`, which refuses statements over 10,000 tokens, e.g. large `bulk_create` inserts).
  - `_call` runs every execute and fetch: `mapped_driver_error()` maps constraint-violation SQLCODEs reported with SQLSTATE HY000 to `IntegrityError`, SQL0913 and HY008 (cancelled) to `OperationalError`, and never attaches statement parameters to errors. Fetches need it too: `SELECT ... FOR UPDATE` waits for row locks in SQLFetch.
  - `OPTIONS['statement_timeout']` cancels the pyodbc cursor from a `threading.Timer` per call. The ODBC query timeout doesn't cover lock waits (they last the file's WAITRCD, live-verified), and Db2 for i has no `SET CURRENT LOCK TIMEOUT`.

### SQL generation

- `operations.py`: date/time SQL (`EXTRACT_FUNCTIONS`, `TRUNC_TIMESTAMP` formats), value converters (booleans stored as SMALLINT, DECFLOAT returned as str, aware datetimes), `limit_offset_sql` (`OFFSET n ROWS FETCH FIRST m ROWS ONLY`), `sql_flush` (drops and re-adds referencing FKs, with `COMMIT`s because of SQL0910). Hook names differ by Django version (e.g. `returning_columns` in 6.0+ vs `return_insert_columns` in 5.2); implement both when Django renames one.
- Time zones: no SQL conversion exists; `_convert_sql_to_tz` raises `NotSupportedError` unless the active zone equals the connection's.
- `compiler.py`: inserts that need generated values become `SELECT cols FROM FINAL TABLE (INSERT ...) ORDER BY INPUT SEQUENCE`; other inserts stay plain. Tables with AFTER INSERT triggers (`ops.has_after_insert_triggers`, cached) use row-by-row plain INSERTs, because FINAL TABLE waits on the record lock and fails (SQL0723) when the trigger changes the row. `"RRN()"` pseudo-column primary keys: `"ALIAS"."RRN()"` is regex-rewritten to `RRN("ALIAS")` in every compiler (covers joins); inserts never return an RRN (RRN() over FINAL TABLE numbers result rows, not records), so new objects keep `pk=None`.
- `fields.py`: `NVarCharField` (`NVARCHAR(n)`, character-length semantics; plain `CharField` on other backends).
- `functions.py`: `as_db2iseries` overrides for expressions (e.g. typed `CASE` results, SQL0580), registered from `base.py`.
- `schemaEditor.py`: switches its job to `INQMSGRPY(*SYSRPYL)` while open (CPA32B2 data-loss inquiries default to cancel, surfacing as HY008) and restores it on exit. Thin subclass of Django's schema editor (`SET DATA TYPE`, `RENAME TABLE`, identity add/drop for auto field changes). Column renames raise `NotSupportedError` (no `RENAME COLUMN` on IBM i).
- `introspection.py`: looks tables up by SQL name in `QSYS2` catalogs (system names accepted for names <= 10 chars); maps native types for `inspectdb` (CCSID 65535 = binary).

### Db2 for i rules that shape the design

- DDL on a table after DML on it in the same transaction fails (SQL0910) -> `can_rollback_ddl = False`.
- Tuple comparisons `(a, b) IN ((?, ?))` / `(a, b) > (?, ?)` are rejected -> `supports_tuple_lookups = False` (composite primary keys).
- `GROUP BY <position>` rejected -> `allows_group_by_select_index = False`.
- `TIME` has no fractional seconds; `CHAR` values come back blank-padded; `VARCHAR(n) CCSID 1208` length counts bytes.

### Other modules

- `compositeKey.py`: legacy viewflow-based `CompositeKey` field (exact lookups only); `CompositePrimaryKey` replaces it.
- `src/management/commands/inspectdb2.py`: customized `inspectdb` (column headings/text as verbose_name/help_text). Not inside the package and not shipped; copy it into an app's `management/commands/` to use it.
