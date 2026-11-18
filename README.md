# django-pyodbc-iseries

Django database backend for **IBM Db2 for i** (IBM i, iSeries, AS/400), using
[pyodbc](https://github.com/mkleehammer/pyodbc) and the IBM i Access ODBC driver.

Forked from [python-ibmdb-django](https://github.com/ibmdb/python-ibmdb-django).

## Requirements

- Python 3.10+ (Django 6.x needs 3.12+)
- Django 5.2 LTS, 6.0 or 6.1
- pyodbc 4.0.39+
- IBM i Access ODBC Driver (part of IBM i Access Client Solutions)
- IBM i 7.3 or later (tested on 7.5); older releases are rejected when connecting
- **Journaled tables** for anything Django writes to (see [Journaling](#journaling-and-commitment-control))

## Installation

```sh
pip install django-pyodbc-iseries
```

From source:

```sh
pip install git+https://github.com/onlysumitg/django-pyodbc-iseries.git
```

## Configuration

```python
DATABASES = {
    'default': {
        'ENGINE': 'django_iseries',
        'NAME': 'mydb',               # relational database name; any value works for *LOCAL
        'HOST': 'myibmi.example.com',
        'USER': 'MYUSER',
        'PASSWORD': '...',
        'CURRENTSCHEMA': 'MYLIB',     # library used for unqualified table names
        'OPTIONS': {
            'dbq': ['MYLIB', 'OTHERLIB'],  # library list (optional)
        },
    }
}

USE_TZ = True
TIME_ZONE = 'UTC'
```

### Settings keys

- `NAME`, `HOST`, `PORT`, `USER`, `PASSWORD`: connection basics. Without `HOST`, `NAME` is used as an ODBC data source name.
- `CURRENTSCHEMA`: runs `SET CURRENT_SCHEMA` after connecting. Django creates and finds tables in this library.
- `SECURITY`, `SSLCLIENTKEYDB`, `SSLCLIENTKEYSTOREDBPASSWORD`, `SSLCLIENTKEYSTASH`, `SSLSERVERCERTIFICATE`: TLS settings, passed to the driver.

### `OPTIONS` keys

- `dbq`: library list, as a list or a comma/space separated string. The first library does **not** become the default schema; use `CURRENTSCHEMA` for that.
- `nam`: naming convention, `0` = `*SQL` (default), `1` = `*SYS`.
- `cmt`: commitment control level (driver `CMT` keyword).
- `trueautocommit`: driver `TRUEAUTOCOMMIT` keyword.
- `driver`: ODBC driver name. Default: `iSeries Access ODBC Driver` on Windows, `IBM i Access ODBC Driver` elsewhere.
- `maxfieldlen`: largest LOB (KB) returned inline. Default `2097152` (the driver maximum). Lower values make the driver use LOB locators, and pyodbc hangs fetching `BLOB` data through a locator.
- `inquiry_message_reply` and `message_replies`: how the connection's server job answers inquiry messages. See [Inquiry messages](#inquiry-messages).
- `statement_timeout`: seconds a database call may run before it is cancelled. Default: off. See [Lock waits and `statement_timeout`](#lock-waits-and-statement_timeout).
- `timeout`, `autocommit`, `readonly`, `ansi`, `encoding`, `attrs_before`: passed to `pyodbc.connect()`.
- Anything else is added to the connection string as `KEY=value`, for example `{'BLOCKSIZE': 512}`.
- Extended dynamic SQL packages are off by default. To use them, name a library that exists:
  `{'XDYNAMIC': 1, 'PKG': 'MYLIB/DJANGO,2,0,0,1,512'}`.

Connection string values are quoted, so passwords may contain `;`, `{` and `}`.

## Inquiry messages

By default the backend doesn't change how the connection's server job (QZDASOINIT) answers inquiry
messages. Two `OPTIONS` change that.

- `inquiry_message_reply`: runs `CHGJOB INQMSGRPY(<value>)` on every new connection.
  - `'*RQD'`: a reply is required; the job waits for one.
  - `'*DFT'`: the message's default reply is sent automatically.
  - `'*SYSRPYL'`: the system reply list is checked for a matching entry and its reply is used; without a
    matching entry, a reply is required.
  - Server jobs are prestarted and **reused by later connections**, from any application, and they keep
    job changes. So the backend reads the job's setting before changing it and puts it back when the
    connection closes. Reading it costs about 0.2 s per new connection; use persistent connections
    (`CONN_MAX_AGE`) to pay it rarely.
  - A connection that ends without being closed (a killed process, a dropped network link) can't restore
    the setting.
- `message_replies`: `(sequence, message_id, reply)` entries added to the system reply list with `ADDRPYLE`.
  - **The system reply list is system wide**: entries apply to every job on the system that uses
    `*SYSRPYL`, and they stay after the connection closes. Adding them needs authority to change the
    system reply list.
  - Each entry is added once per process.
  - Setting `message_replies` also sets `inquiry_message_reply` to `'*SYSRPYL'` unless you set it yourself,
    because the job only uses the reply list with that setting.
  - The sequence must be 1-9999, the message id like `CPA32B2` (or `*ANY`), and the reply up to 32 letters
    or digits (or `*DFT`, `*RQD`). Other values raise `ImproperlyConfigured`.

```python
'OPTIONS': {
    'inquiry_message_reply': '*SYSRPYL',
    'message_replies': [(9001, 'CPA32B2', 'I')],  # answer CPA32B2 with reply I
}
```

If a command fails (for example, missing authority), it is logged as a warning on the `django_iseries`
logger and the connection is still used.

### Migrations and inquiry message CPA32B2

Schema changes that can lose data (dropping a column, changing its type) send inquiry message `CPA32B2`
("may cause data to be lost") to the job. Its default reply is `C` (cancel), so the statement is cancelled
unless the system reply list answers it with `I`.

- While a schema change runs, the backend switches its job to `INQMSGRPY(*SYSRPYL)` and afterwards
  restores the previous setting, whatever `inquiry_message_reply` says.
- The system reply list needs an entry for `CPA32B2` with reply `I`. Many systems have one; check with
  `SELECT * FROM QSYS2.REPLY_LIST_INFO WHERE MESSAGE_ID = 'CPA32B2'`, or add one with
  `ADDRPYLE SEQNBR(9001) MSGID(CPA32B2) RPY(I)` (or `message_replies`).
- Without it, the migration fails with `OperationalError` naming `CPA32B2`.

## Journaling and commitment control

Django runs every write under commitment control. Db2 for i only allows that on **journaled** tables;
otherwise writes fail with:

```text
SQL7008 - <table> in <library> not valid for operation.
```

Tables created by `CREATE SCHEMA` libraries are journaled automatically. For a library created with
`CRTLIB`, start journaling once:

```text
CRTJRNRCV JRNRCV(MYLIB/QSQJRN0001)
CRTJRN    JRN(MYLIB/QSQJRN) JRNRCV(MYLIB/QSQJRN0001)
STRJRNLIB LIB(MYLIB) JRN(MYLIB/QSQJRN)                          /* new tables */
STRJRNPF  FILE(MYLIB/*ALL) JRN(MYLIB/QSQJRN) IMAGES(*BOTH)      /* existing tables */
```

If journaling isn't possible, `'OPTIONS': {'cmt': 0}` turns commitment control off. Transactions and
savepoints then no longer roll anything back.

## Transactions and isolation

The backend connects with read committed isolation and "currently committed" reads:
`CMT=1` (`*CS`) and `CONCURRENTACCESSRESOLUTION=1`.

- Inside a transaction (`atomic()`, `ATOMIC_REQUESTS`), reads see only committed data, and they don't wait
  for rows another transaction is changing: they get the last committed version.
- The driver's own default, `CMT=2` (`*CHG`), lets reads see other transactions' uncommitted changes.
- **Autocommit statements (outside `atomic()`) run without commitment control**, so autocommit reads can
  still see uncommitted changes. `'OPTIONS': {'trueautocommit': 1}` runs them under the same isolation,
  but then every table written outside a transaction must be journaled too (see below).
- `OPTIONS['cmt']` and `OPTIONS['concurrentaccessresolution']` override the defaults (`0` = `*NONE`,
  `1` = `*CS`, `2` = `*CHG`, `3` = `*ALL`, `4` = `*RR`).

If a client disappears in the middle of a long statement (killed process, network timeout), its server job
keeps running the statement and holds its locks until it notices; its transaction is then rolled back.
Other connections can get `OperationalError` (SQL0913, "in use") meanwhile. Keep statements short, and look
for `QZDASOINIT` jobs in `QUSRWRK` that outlive their clients.

### Lock waits and `statement_timeout`

A statement that needs a row another transaction has locked waits for the file's record wait time (`WAITRCD`,
60 seconds by default), then fails with `OperationalError` (SQL0913). `SELECT ... FOR UPDATE`
(`select_for_update()`) waits while fetching the row rather than while executing.

- The ODBC query timeout (pyodbc `Connection.timeout`) **does not** shorten lock waits, and Db2 for i has no
  `SET CURRENT LOCK TIMEOUT`.
- `'OPTIONS': {'statement_timeout': 30}` cancels any database call (execute or fetch) that runs longer than
  30 seconds. The cancelled call raises `OperationalError` with SQLSTATE `HY008`; roll back the transaction
  as usual, and the connection stays usable.
- The limit applies **to each call**: each execute and each fetch gets the full time. It also stops long
  queries that aren't waiting for a lock.
- It applies to everything run through Django on that connection, including migrations; leave it off for
  connections that run long schema changes.
- To skip locked rows instead of waiting, use `select_for_update(skip_locked=True)`.

```python
'OPTIONS': {
    'statement_timeout': 30,  # seconds; 0 or None turns it off
}
```

```python
from django.db import OperationalError, transaction

try:
    with transaction.atomic(using='r1'):
        order = Order.objects.using('r1').select_for_update().get(pk=order_no)
        ...
except OperationalError as e:
    if not (e.args and e.args[0] == 'HY008'):
        raise
    ...  # cancelled by statement_timeout, most likely while waiting for a lock
```

## Time zones

Db2 for i has no time zone support: timestamps are stored without an offset, in the connection's
time zone (`DATABASES['default']['TIME_ZONE']`, default UTC).

- `USE_TZ = True`: aware datetimes are converted to the connection time zone when saved and come back aware.
- Date functions and lookups on datetimes (`__date`, `TruncDay`, `ExtractHour`, ...) run in SQL, which
  can't convert between time zones. They work when the current time zone equals the connection time zone,
  and raise `NotSupportedError` otherwise.
- Legacy data stored in local time: set `DATABASES['default']['TIME_ZONE']` to that zone.

## Composite primary keys

Django 5.2+ `CompositePrimaryKey` is fully supported (create, read, update, delete, bulk operations,
`pk__in` and range lookups):

```python
class OrderLine(models.Model):
    pk = models.CompositePrimaryKey('order_no', 'line_no')
    order_no = models.IntegerField()
    line_no = models.IntegerField()
    product = models.CharField(max_length=30)

line = OrderLine.objects.get(pk=(1, 2))
OrderLine.objects.filter(pk__in=[(1, 1), (1, 2)]).delete()
```

`django_iseries.compositeKey.CompositeKey`, the older composite key field, only supports exact
lookups. Prefer `CompositePrimaryKey` for new code.

## Existing tables

- Map them with `managed = False` models; `python manage.py inspectdb MYTABLE` generates a starting point.
- Tables are found by their SQL name. System names (for example `CUSTO00001`) work in `db_table` too.
- `CHAR` and `GRAPHIC` columns are fixed length: values come back padded with blanks. Comparisons ignore
  the padding, so `filter(code='AB')` matches `'AB   '`.
- Physical files report as tables; logical files, aliases and materialized query tables as views.
- Generated keys are read with `SELECT ... FROM FINAL TABLE (INSERT ...)`. Tables with `AFTER INSERT`
  triggers (SQL or external) are detected and use plain `INSERT` plus `IDENTITY_VAL_LOCAL()` instead,
  one row per statement for `bulk_create`. Trigger detection is cached per process: restart after
  adding a trigger to a table the application already wrote to.

## Character lengths: `CharField` and `NVarCharField`

`CharField(max_length=n)` is created as `VARCHAR(n) CCSID 1208` (UTF-8), the usual Unicode character type
on IBM i. Db2 counts its length in **bytes**: ASCII text takes one byte per character, `é` two and `€` or
`日` three, so non-ASCII text holds fewer than `n` characters. Values that don't fit raise `DataError`;
nothing is truncated.

For columns that must hold `n` characters of any script, use `NVarCharField`, stored as `NVARCHAR(n)`
(UTF-16, CCSID 1200):

```python
from django_iseries.fields import NVarCharField

class Customer(models.Model):
    code = models.CharField(max_length=10)   # VARCHAR(10) CCSID 1208
    name = NVarCharField(max_length=50)      # NVARCHAR(50): 50 characters of any script
```

- Characters outside the Basic Multilingual Plane (such as emoji) take two of the `n` positions.
- It uses two bytes per character, and RPG programs see the column as UCS-2 graphic data.
- On other databases it behaves exactly like `CharField`, so migrations stay portable.
- Changing an existing `CharField` to `NVarCharField` in a migration converts the column in place (`SET DATA TYPE`).
- `inspectdb` generates `CharField(max_length=n)` for existing `NVARCHAR`/`VARGRAPHIC` columns, which reads and
  writes them correctly; switch to `NVarCharField` if Django should create or alter the column.

## Record number (RRN) keys

Keyless legacy files can be mapped with the relative record number as the primary key:

```python
class LegacyRecord(models.Model):
    id = models.AutoField(primary_key=True, db_column='RRN()')
    name = models.CharField(max_length=10)

    class Meta:
        managed = False
        db_table = 'MYFILE'
```

- Reads, filters, ordering, updates, deletes and joins (`select_related`, filters across relations)
  use `RRN(table)`.
- **New rows don't get a primary key**: `create()` and `bulk_create()` insert the rows but leave `pk` as
  `None`, because the record number of an inserted row can't be read back. Query the row again to get it.
- **Record numbers aren't stable keys.** Reorganizing the file (`RGZPFM`) renumbers records, and with
  `REUSEDLT(*YES)` a deleted record's number is given to the next new row. An object loaded before
  either happened updates or deletes whatever record now has its number. Prefer a real unique key;
  use RRN keys for read-mostly access, and re-read rows shortly before changing them.

## Data types

| Django field | Column created | Notes |
|---|---|---|
| `AutoField`, `BigAutoField`, `SmallAutoField` | `INTEGER` / `BIGINT` / `SMALLINT` identity | |
| `CharField` | `VARCHAR(n) CCSID 1208` | `n` counts UTF-8 bytes |
| `django_iseries.fields.NVarCharField` | `NVARCHAR(n)` | `n` counts characters |
| `TextField` | `CLOB CCSID 1208` | |
| `BooleanField` | `SMALLINT` with a check | native `BOOLEAN` columns are read too |
| `IntegerField`, `BigIntegerField`, `SmallIntegerField` | `INTEGER`, `BIGINT`, `SMALLINT` | |
| `DecimalField` | `DECIMAL(p, s)` | `NUMERIC` and `DECFLOAT` columns are read too |
| `FloatField` | `DOUBLE` | |
| `DateField`, `TimeField`, `DateTimeField` | `DATE`, `TIME`, `TIMESTAMP` | `TIME` has no fractional seconds |
| `BinaryField` | `BLOB` | `BINARY`, `VARBINARY`, `FOR BIT DATA` columns are read too |
| `UUIDField` | `VARCHAR(255)` | |
| `DurationField` | `DOUBLE` | microseconds |
| `JSONField` | not supported | |

`inspectdb` also recognizes `GRAPHIC`/`VARGRAPHIC`/`NCHAR`/`NVARCHAR` (`CharField`), `DBCLOB`/`XML`
(`TextField`), `ROWID` (`BinaryField`) and identity columns (auto fields).

## Testing your project

`manage.py test` (and pytest-django) runs tests in a **separate schema**, never in `CURRENTSCHEMA`:

- By default the test schema is `TEST_<CURRENTSCHEMA>`, created with `CREATE SCHEMA` (so its tables are
  journaled) and dropped afterwards. `--keepdb` keeps it between runs.
- Running tests in `CURRENTSCHEMA` itself is refused with `ImproperlyConfigured`.
- `TEST['SCHEMA']` chooses the name. If your profile can't create libraries, have one created and set
  `TEST['CREATE_DB'] = False`: it is then used as is and never dropped (run with `--keepdb`, or start
  from an empty schema).

```python
DATABASES = {
    'default': {
        'ENGINE': 'django_iseries',
        # ...
        'CURRENTSCHEMA': 'MYLIB',
        'TEST': {'SCHEMA': 'MYLIBTEST'},   # optional; default TEST_MYLIB
    }
}
```

- Parallel test runs aren't supported: with `--parallel`, Django stops with "The database backend doesn't
  support cloning databases". Run tests serially (`--parallel 1`).

## Limitations

- **Column renames are not supported**: Db2 for i has no `RENAME COLUMN`. Keep the column by setting
  `db_column` to its current name when renaming a field.
- **Migrations are not atomic**: Db2 for i can't change a table's definition after changing its rows in the
  same transaction, and Django's schema changes do both. Each schema statement commits on its own, so a
  migration that fails part way can leave part of its changes applied.
- `CharField(max_length=n)` holds `n` **bytes** of UTF-8: fewer characters when text isn't ASCII (use `NVarCharField` where that matters).
- `TimeField` stores whole seconds.
- `select_for_update(nowait=True)`, `JSONField`, `dbshell` and time zone conversion in SQL are not supported.

## Running the tests

```sh
pip install -e ".[test]"
cp .env.example .env    # then fill in a dedicated, empty, journaled test library
pytest
```

- Tests that need IBM i are skipped when `TEST_SYSTEM_HOST` is not set.
- The test library is used directly: tests create tables there and empty them afterwards. Never point it at
  a library with real data.
