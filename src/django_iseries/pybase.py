# +--------------------------------------------------------------------------+
# |  Licensed Materials - Property of IBM                                    |
# |                                                                          |
# | (C) Copyright IBM Corporation 2009-2018.                                      |
# +--------------------------------------------------------------------------+
# | This module complies with Django 1.0 and is                              |
# | Licensed under the Apache License, Version 2.0 (the "License");          |
# | you may not use this file except in compliance with the License.         |
# | You may obtain a copy of the License at                                  |
# | http://www.apache.org/licenses/LICENSE-2.0 Unless required by applicable |
# | law or agreed to in writing, software distributed under the License is   |
# | distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY |
# | KIND, either express or implied. See the License for the specific        |
# | language governing permissions and limitations under the License.        |
# +--------------------------------------------------------------------------+
# | Authors: Ambrish Bhargava, Tarun Pasrija, Rahul Priyadarshi              |
# +--------------------------------------------------------------------------+


import decimal
import logging
import platform
import re
import threading

from functools import partial
from typing import Optional

from django.core.exceptions import ImproperlyConfigured
from django.db import utils

from django_iseries import Database

DatabaseError = Database.DatabaseError
IntegrityError = Database.IntegrityError
Error = Database.Error
InterfaceError = Database.InterfaceError
DataError = Database.DataError
OperationalError = Database.OperationalError
InternalError = Database.InternalError
ProgrammingError = Database.ProgrammingError
NotSupportedError = Database.NotSupportedError

logger = logging.getLogger('django_iseries')

FORMAT_QMARK_REGEX = re.compile(r'(?<!%)%s')
# Tokens that matter for finding parameter markers in select lists. String literals, quoted
# identifiers and comments are matched as a whole so that '?', SELECT or FROM inside them are skipped.
SQL_SCAN_REGEX = re.compile(
    r"'(?:[^']|'')*'"          # string literal
    r'|"(?:[^"]|"")*"'          # quoted identifier
    r'|--[^\n]*'                # line comment
    r'|/\*.*?\*/'              # block comment
    r'|\?'                      # parameter marker
    r'|\b(?:SELECT|FROM)\b',   # clause keywords
    re.IGNORECASE | re.DOTALL,
)
SQLCODE_REGEX = re.compile(r"^(\[.+] *){4}SQL(\d{4})")
# Constraint violations iaccess may report with SQLSTATE HY000: null in a NOT NULL column (0407),
# referential constraints (0530-0532), check constraints (0545) and duplicate keys (0803).
INTEGRITY_SQLCODES = {'0407', '0530', '0531', '0532', '0545', '0803'}
# Row or object in use: a lock wait that timed out.
LOCK_SQLCODES = {'0913'}
# Operation cancelled, for example by OPTIONS['statement_timeout'].
CANCELLED_SQLSTATE = 'HY008'
SQLCODE_0910_REGEX = re.compile(r"^(\[.+] *){4}SQL0910.*")


# Keyword arguments pyodbc.connect() handles itself; any other OPTIONS entry is added to the
# connection string as KEY=value.
PYODBC_CONNECT_KWARGS = {'autocommit', 'ansi', 'timeout', 'readonly', 'attrs_before', 'encoding'}

# settings / OPTIONS key -> IBM i Access ODBC connection string keyword.
CONNECTION_STRING_KEYWORDS = {
    'security': 'SECURITY',
    'sslclientkeydb': 'SSLCLIENTKEYSTOREDB',
    'sslclientkeystoredb': 'SSLCLIENTKEYSTOREDB',
    'sslclientkeystoredbpassword': 'SSLCLIENTKEYSTOREDBPASSWORD',
    'sslclientkeystash': 'SSLCLIENTKEYSTASH',
    'sslservercertificate': 'SSLSERVERCERTIFICATE',
    'nam': 'NAM',  # naming: 0 = *SQL (default), 1 = *SYS
    'cmt': 'CMT',  # commitment control level
    'trueautocommit': 'TRUEAUTOCOMMIT',
    'concurrentaccessresolution': 'CONCURRENTACCESSRESOLUTION',
}

# Defaults that differ from the driver's: read committed (*CS) instead of *CHG, which lets reads see other
# transactions' uncommitted changes, and "currently committed" so readers get the last committed row
# instead of waiting for a writer's lock. They apply to statements in transactions; with autocommit the
# driver runs statements without commitment control unless TRUEAUTOCOMMIT=1.
CONNECTION_DEFAULTS = {
    'cmt': 1,
    'concurrentaccessresolution': 1,
}


def odbc_connection_string_value(value):
    """Quote a connection string value when it contains characters with special meaning (ODBC rules)."""
    value = str(value)
    if any(char in value for char in ';{}=') or value != value.strip():
        return '{' + value.replace('}', '}}') + '}'
    return value


# Largest LOB (in KB) returned with the result data instead of through a LOB locator. pyodbc hangs
# fetching a BLOB through a locator, which happens for BLOB columns larger than the driver default
# (32 KB), so the driver maximum is used unless OPTIONS['maxfieldlen'] says otherwise.
DEFAULT_MAX_FIELD_LENGTH = 2097152


INQUIRY_MESSAGE_REPLIES = ('*RQD', '*DFT', '*SYSRPYL')
MESSAGE_ID_REGEX = re.compile(r'^([A-Z]{3}[0-9A-F]{4}|\*ANY)$')
REPLY_REGEX = re.compile(r'^(\*DFT|\*RQD|[A-Z0-9]{1,32})$')

# System reply list entries are system wide and persistent, so each is added once per process.
_added_reply_list_entries = set()


def validated_inquiry_message_reply(value):
    if value is None:
        return None
    value = str(value).upper()
    if value not in INQUIRY_MESSAGE_REPLIES:
        raise ImproperlyConfigured(
            f"OPTIONS['inquiry_message_reply'] must be one of {', '.join(INQUIRY_MESSAGE_REPLIES)}; got {value!r}."
        )
    return value


def validated_message_replies(message_replies):
    """Check (sequence, message id, reply) entries, since they are spliced into a CL command."""
    validated = []
    for entry in message_replies:
        try:
            seq, message_id, reply = entry
            seq = int(seq)
        except (TypeError, ValueError):
            raise ImproperlyConfigured(
                f"OPTIONS['message_replies'] entries are (sequence, message_id, reply); got {entry!r}."
            )
        message_id, reply = str(message_id).upper(), str(reply).upper()
        if not (1 <= seq <= 9999 and MESSAGE_ID_REGEX.match(message_id) and REPLY_REGEX.match(reply)):
            raise ImproperlyConfigured(
                f"Invalid OPTIONS['message_replies'] entry {entry!r}: the sequence must be 1-9999, the message id "
                f"like CPA32B2 (or *ANY), and the reply up to 32 letters or digits (or *DFT, *RQD)."
            )
        validated.append((seq, message_id, reply))
    return validated


def run_cl_command(cursor, command):
    """Run a CL command; a failure is logged, not raised, so it doesn't prevent connecting."""
    try:
        cursor.execute("{call QSYS2.QCMDEXC('%s')}" % command)
    except Database.Error as e:
        logger.warning('IBM i command %s failed: %s', command, e)
        return False
    return True


# The connection's own job. JOB_INFO is filtered on the job's user (the middle part of the job name)
# because scanning all users' jobs is slower.
JOB_INQUIRY_MESSAGE_REPLY_SQL = (
    "SELECT INQUIRY_MESSAGE_REPLY FROM TABLE(QSYS2.JOB_INFO(JOB_USER_FILTER => "
    "SUBSTR(QSYS2.JOB_NAME, LOCATE('/', QSYS2.JOB_NAME) + 1, "
    "LOCATE('/', QSYS2.JOB_NAME, LOCATE('/', QSYS2.JOB_NAME) + 1) - LOCATE('/', QSYS2.JOB_NAME) - 1))) X "
    "WHERE JOB_NAME = QSYS2.JOB_NAME"
)


def job_inquiry_message_reply(cursor):
    """The connection's job INQMSGRPY setting, or None if it can't be read."""
    try:
        cursor.execute(JOB_INQUIRY_MESSAGE_REPLY_SQL)
        row = cursor.fetchone()
    except Database.Error as e:
        logger.warning("Couldn't read the job's inquiry message reply: %s", e)
        return None
    value = row[0].strip() if row and row[0] else None
    return value if value in INQUIRY_MESSAGE_REPLIES else None


def add_system_reply_list_entries(cursor, message_replies):
    for seq, message_id, reply in message_replies:
        if (seq, message_id, reply) in _added_reply_list_entries:
            continue
        if run_cl_command(cursor, f'ADDRPYLE SEQNBR({seq}) MSGID({message_id}) RPY({reply})'):
            _added_reply_list_entries.add((seq, message_id, reply))


def validated_statement_timeout(value):
    """Seconds as a float, or None when the timeout is off (unset or 0)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ImproperlyConfigured(
            f"OPTIONS['statement_timeout'] must be a number of seconds (0 or None turns it off); got {value!r}."
        )
    return float(value) or None


def default_driver_name():
    return 'iSeries Access ODBC Driver' if platform.system() == 'Windows' else 'IBM i Access ODBC Driver'


class DatabaseWrapper:
    statement_timeout = None

    def get_new_connection(self, kwargs):
        kwargs = dict(kwargs)
        options = dict(kwargs.pop('options', None) or {})
        currentschema = kwargs.pop('currentschema', None)
        self.statement_timeout = validated_statement_timeout(options.pop('statement_timeout', None))
        message_replies = validated_message_replies(options.pop('message_replies', None) or [])
        # The job only uses the system reply list (and so message_replies) with INQMSGRPY(*SYSRPYL).
        inquiry_message_reply = validated_inquiry_message_reply(
            options.pop('inquiry_message_reply', '*SYSRPYL' if message_replies else None))
        kwargs.pop('PCONNECT', None)  # ibm_db persistent connections; not applicable to pyodbc

        attributes = []
        if kwargs.get('host'):
            attributes += [
                ('DRIVER', options.pop('driver', None) or default_driver_name()),
                ('DATABASE', kwargs.get('database')),
                ('UNICODESQL', 1),
                ('SYSTEM', kwargs.get('host')),
            ]
            if kwargs.get('port'):
                attributes.append(('PORT', kwargs['port']))
            attributes += [
                ('PROTOCOL', 'TCPIP'),
                ('UID', kwargs.get('user', '')),
                ('PWD', kwargs.get('password', '')),
                ('MAXFIELDLEN', options.pop('maxfieldlen', DEFAULT_MAX_FIELD_LENGTH)),
            ]
        else:
            # No host: NAME is an ODBC data source name.
            attributes.append(('DSN', kwargs.get('database')))

        options_lower = {key.lower() for key in options}
        for key, value in CONNECTION_DEFAULTS.items():
            if key not in options_lower:
                options[key] = value
        options = {
            (key.lower() if key.lower() in CONNECTION_STRING_KEYWORDS else key): value for key, value in options.items()
        }
        settings_and_options = {**kwargs, **options}
        for key, keyword in CONNECTION_STRING_KEYWORDS.items():
            if settings_and_options.get(key) is not None:
                attributes.append((keyword, settings_and_options[key]))
            options.pop(key, None)

        if 'dbq' in options:
            # Library list. The leading comma keeps the first library from becoming the default schema.
            libraries = options.pop('dbq') or []
            if isinstance(libraries, str):
                libraries = [library for library in re.split(r'[\s,]+', libraries) if library]
            attributes.append(('DBQ', ',' + ','.join(libraries)))

        connect_kwargs = {'autocommit': False}
        for key, value in options.items():
            if key in PYODBC_CONNECT_KWARGS:
                connect_kwargs[key] = value
            else:
                attributes.append((key, value))

        connection_string = ''.join(
            f'{keyword}={odbc_connection_string_value(value)};' for keyword, value in attributes
        )
        connection = Database.connect(connection_string, **connect_kwargs)

        cursor = connection.cursor()
        try:
            if currentschema:
                cursor.execute(f'SET CURRENT_SCHEMA = {currentschema}')
            add_system_reply_list_entries(cursor, message_replies)
            if inquiry_message_reply:
                # Server jobs (QZDASOINIT) are reused by later connections, from any application, and keep
                # job changes; remember the job's setting so close() can put it back.
                self.restore_inquiry_message_reply = job_inquiry_message_reply(cursor)
                run_cl_command(cursor, f'CHGJOB INQMSGRPY({inquiry_message_reply})')
        finally:
            cursor.close()
        return connection

    def is_active(self, connection):
        try:
            cursor = connection.cursor()
            try:
                cursor.execute('SELECT 1 FROM SYSIBM.SYSDUMMY1')
            finally:
                cursor.close()
        except Database.Error:
            return False
        return True

    # Over-riding _cursor method to return DB2 cursor.
    def _cursor(self, connection):
        return DB2CursorWrapper(connection, statement_timeout=self.statement_timeout)

    def close(self, connection):
        restore = getattr(self, 'restore_inquiry_message_reply', None)
        if restore:
            self.restore_inquiry_message_reply = None
            try:
                cursor = connection.cursor()
                try:
                    run_cl_command(cursor, f'CHGJOB INQMSGRPY({restore})')
                finally:
                    cursor.close()
            except Database.Error as e:
                logger.warning("Couldn't restore the job's inquiry message reply (%s): %s", restore, e)
        try:
            connection.close()
        except ProgrammingError as e:
            if str(e) == 'Attempt to use a closed connection.':
                pass
            else:
                raise

    def get_server_version(self, connection):
        # For example '07.05.0000' -> (7, 5, 0)
        return tuple(int(part) for part in connection.getinfo(Database.SQL_DBMS_VER).split('.'))


class DB2CursorWrapper:
    """
    This is the wrapper around IBM_DB_DBI in order to support format parameter style
    IBM_DB_DBI supports qmark, where as Django support format style,
    hence this conversion is required.

    pyodbc.Cursor cannot be subclassed, so we store it as an attribute
    """

    current_schema = None

    def __init__(self, connection, statement_timeout=None):
        self.cursor: Database.Cursor = connection.cursor()
        self.statement_timeout = statement_timeout

    def __iter__(self):
        return self.cursor

    def __getattr__(self, attr):
        return getattr(self.cursor, attr)

    def get_current_schema(self):
        if self.current_schema is not None:
            return self.current_schema.upper()
        self.execute('select CURRENT_SCHEMA from sysibm.sysdummy1')
        self.current_schema = self.fetchone()[0]
        return self.current_schema.upper()

    def set_current_schema(self, schema):
        self.execute(f'SET CURRENT_SCHEMA = {schema}')

    def close(self):
        """
        Django calls close() twice on some cursors, but pyodbc does not allow this.
        pyodbc deletes the 'connection' attribute when closing a cursor, so we check for that.

        In the unlikely event that this code prevents close() from being called, pyodbc will close
        the cursor automatically when it goes out of scope.
        """
        if getattr(self, 'connection', False):
            self.cursor.close()

    def execute(self, query, params = ()):
        if params:
            query = self.convert_query(query)
            query, params = self._replace_placeholders_in_select_clause(params, query)
        result = self._call(partial(self.cursor.execute, query, params), query)
        return result

    def executemany(self, query, param_list):
        if not param_list:
            # empty param_list means do nothing (execute the query zero times)
            return
        query = self.convert_query(query)
        result = self._call(partial(self.cursor.executemany, query, param_list), query)
        return result

    def _call(self, driver_call, query=None):
        """
        Run a driver call (execute or fetch) under the statement timeout, mapping driver errors.

        Errors are re-raised without the statement parameters attached, so parameter values
        (which may hold personal data or secrets) don't leak into logs and error reports.
        """
        timer = None
        if self.statement_timeout:
            # Cancelling from another thread is how ODBC interrupts a running call; the call then
            # fails with HY008. Lock waits ignore the ODBC query timeout, so it can't be used instead.
            timer = threading.Timer(self.statement_timeout, self.cursor.cancel)
            timer.daemon = True
            timer.start()
        try:
            result = driver_call()
        except Database.Error as e:
            mapped = mapped_driver_error(e, query)
            if mapped is e:
                raise
            raise mapped from e
        finally:
            if timer is not None:
                timer.cancel()
        if result is self.cursor:
            return self
        return result

    def _replace_placeholders_in_select_clause(self, params, query):
        """
        Db2 for i rejects untyped parameter markers in the select list (SQL0418), so parameters
        there are inlined as SQL literals; all other parameters stay bound.
        """
        if '?' not in query:
            return query, list(params)
        bound_params = []
        param_iter = iter(params)
        in_select_clause = False
        parts = []
        position = 0
        for match in SQL_SCAN_REGEX.finditer(query):
            token = match.group()
            if token == '?':
                value = next(param_iter)
                if in_select_clause:
                    parts.append(query[position:match.start()])
                    parts.append(self.quote_value(value))
                    position = match.end()
                else:
                    bound_params.append(value)
            elif token.upper() == 'SELECT':
                in_select_clause = True
            elif token.upper() == 'FROM':
                in_select_clause = False
        parts.append(query[position:])
        bound_params.extend(param_iter)
        return ''.join(parts), bound_params

    def convert_query(self, query):
        """
        Django uses "format" style placeholders, but the iaccess odbc driver uses "qmark" style.
        This fixes it -- but note that if you want to use a literal "%s" in a query,
        you'll need to use "%%s".
        """
        return FORMAT_QMARK_REGEX.sub('?', query).replace('%%', '%')

    def _row_factory(self, row: Optional[Database.Row]):
        if row is None:
            return row
        return tuple(row)

    # Fetches go through _call too: a lock wait can happen while fetching (SELECT ... FOR UPDATE
    # waits for the row lock in SQLFetch), and the statement timeout has to cover that wait.

    def fetchone(self):
        return self._row_factory(self._call(self.cursor.fetchone))

    def fetchmany(self, size):
        return [self._row_factory(row) for row in self._call(partial(self.cursor.fetchmany, size))]

    def fetchall(self):
        return [self._row_factory(row) for row in self._call(self.cursor.fetchall)]

    @property
    def last_identity_val(self):
        result = self.execute('SELECT IDENTITY_VAL_LOCAL() AS IDENTITY FROM SYSIBM.SYSDUMMY1')
        row = result.fetchone()
        return row[0]

    def quote_value(self, value):
        return sql_literal(value)


def mapped_driver_error(e, query=None):
    """
    The error to raise for a pyodbc error: the error itself, or one whose class tells Django what happened.
    `query` is the statement being executed, or None for fetches.
    """
    sqlstate = e.args[0] if e.args else None
    message = e.args[1] if len(e.args) > 1 else ''
    if sqlstate == CANCELLED_SQLSTATE and not isinstance(e, Database.OperationalError):
        # pyodbc raises its base Error for HY008, which `except DatabaseError` would miss.
        return Database.OperationalError(*e.args)
    if sqlstate != 'HY000':
        return e
    # iaccess sends SQLSTATE HY000 for some errors; map them to the right Django error.
    sqlcode = SQLCODE_REGEX.match(message)
    if sqlcode and sqlcode.group(2) in INTEGRITY_SQLCODES:
        return utils.IntegrityError(*e.args)
    if query and SQLCODE_0910_REGEX.match(message) and query.startswith('ALTER TABLE') and 'RESTART WITH' in query:
        # SQL0910: file in use (likely in the same transaction)
        return utils.ProgrammingError(
            *e.args,
            "Db2 for iSeries cannot reset a table's primary key sequence during same "
            "transaction as insert/update on that table"
        )
    if type(e) is Database.Error:
        # pyodbc raises its base Error for SQLSTATE HY000, which Django doesn't treat as a
        # DatabaseError, so `except DatabaseError` would miss it.
        if sqlcode and sqlcode.group(2) in LOCK_SQLCODES:
            return Database.OperationalError(*e.args)
        return Database.DatabaseError(*e.args)
    return e


def sql_literal(value):
    """Render a Python value as a Db2 for i SQL literal."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return format(value, 'f')
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"BX'{bytes(value).hex().upper()}'"
    # Strings, dates, times, UUIDs and anything else: a quoted string with quotes doubled.
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
