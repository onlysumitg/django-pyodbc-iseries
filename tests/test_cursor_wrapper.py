"""
Regression tests for DB2CursorWrapper that run without an IBM i connection.

The wrapped pyodbc cursor is replaced by a stub that records what would be sent to the driver.
"""
import datetime
import decimal
import threading
import time

import pyodbc
import pytest
from django.core.exceptions import ImproperlyConfigured

from django_iseries.pybase import DB2CursorWrapper, validated_statement_timeout

CANCELLED = pyodbc.Error('HY008', '[HY008] [IBM][System i Access ODBC Driver]Operation cancelled. (30071)')


class RecordingCursor:
    """
    Stands in for pyodbc.Cursor: records executed statements or raises a given error.
    With block=True, execute and fetches wait until cancel() is called and then fail like the driver (HY008).
    """

    def __init__(self, error=None, fetch_error=None, block=False):
        self.error = error
        self.fetch_error = fetch_error
        self.block = block
        self.cancelled = threading.Event()
        self.executed = []

    def _wait_if_blocking(self):
        if self.block:
            if not self.cancelled.wait(timeout=5):
                raise AssertionError('the call was never cancelled')
            raise CANCELLED

    def execute(self, query, params):
        if self.error is not None:
            raise self.error
        self._wait_if_blocking()
        self.executed.append((query, list(params)))
        return self

    def _fetch(self, rows):
        if self.fetch_error is not None:
            raise self.fetch_error
        self._wait_if_blocking()
        return rows

    def fetchone(self):
        return self._fetch((1,))

    def fetchmany(self, size):
        return self._fetch([(1,)])

    def fetchall(self):
        return self._fetch([(1,)])

    def cancel(self):
        self.cancelled.set()


class StubConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def make_wrapper(error=None, fetch_error=None, block=False, statement_timeout=None):
    recording_cursor = RecordingCursor(error, fetch_error=fetch_error, block=block)
    wrapper = DB2CursorWrapper(StubConnection(recording_cursor), statement_timeout=statement_timeout)
    return wrapper, recording_cursor


def test_placeholders_outside_select_clause_are_bound():
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('SELECT "A" FROM t WHERE x = %s AND y = %s', ('one', 'two'))
    assert recording_cursor.executed == [('SELECT "A" FROM t WHERE x = ? AND y = ?', ['one', 'two'])]


def test_multiple_select_clause_params_keep_their_order():
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('SELECT %s AS a, %s AS b FROM t WHERE x = %s', ('A', 'B', 'C'))
    assert recording_cursor.executed == [("SELECT 'A' AS a, 'B' AS b FROM t WHERE x = ?", ['C'])]


def test_select_clause_string_param_is_escaped():
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('SELECT %s AS a FROM t', ("x' OR '1'='1",))
    assert recording_cursor.executed == [("SELECT 'x'' OR ''1''=''1' AS a FROM t", [])]


def test_select_clause_none_param_becomes_null():
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('SELECT %s AS a FROM t', (None,))
    assert recording_cursor.executed == [('SELECT NULL AS a FROM t', [])]


def test_non_driver_error_propagates_unchanged():
    wrapper, _ = make_wrapper(error=ValueError('boom'))
    with pytest.raises(ValueError, match='boom'):
        wrapper.execute('SELECT "A" FROM t WHERE x = %s', ('one',))


@pytest.mark.parametrize('value,literal', [
    (True, '1'),
    (False, '0'),
    (42, '42'),
    (decimal.Decimal('1E+2'), '100'),
    (b'\x00\xff', "BX'00FF'"),
    (datetime.date(2024, 5, 17), "'2024-05-17'"),
])
def test_select_clause_literals_by_type(value, literal):
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('SELECT %s AS a FROM t', (value,))
    assert recording_cursor.executed == [(f'SELECT {literal} AS a FROM t', [])]


def test_driver_errors_do_not_carry_statement_parameters():
    error = pyodbc.Error('HY000', '[HY000] some driver error')
    wrapper, _ = make_wrapper(error=error)
    with pytest.raises(pyodbc.Error) as exc_info:
        wrapper.execute('SELECT "A" FROM t WHERE x = %s', ('secret-value',))
    assert 'secret-value' not in str(exc_info.value.args)


@pytest.mark.parametrize('sqlcode', ['0407', '0530', '0545', '0803'])
def test_constraint_violations_reported_as_hy000_raise_integrity_error(sqlcode):
    from django.db import utils

    message = f'[HY000] [IBM][System i Access ODBC Driver][DB2 for i5/OS]SQL{sqlcode} - violation. (-{sqlcode})'
    wrapper, _ = make_wrapper(error=pyodbc.Error('HY000', message))
    with pytest.raises(utils.IntegrityError):
        wrapper.execute('UPDATE t SET x = %s', (1,))


@pytest.mark.parametrize('sqlcode,expected', [
    ('0101', pyodbc.DatabaseError),     # statement too long or complex
    ('0913', pyodbc.OperationalError),  # row or object in use (lock wait)
])
def test_generic_hy000_errors_become_database_errors(sqlcode, expected):
    message = f'[HY000] [IBM][System i Access ODBC Driver][DB2 for i5/OS]SQL{sqlcode} - failed. (-{sqlcode})'
    wrapper, _ = make_wrapper(error=pyodbc.Error('HY000', message))
    with pytest.raises(expected) as exc_info:
        wrapper.execute('SELECT "A" FROM t WHERE x = %s', (1,))
    assert type(exc_info.value) is expected
    assert exc_info.value.args == ('HY000', message)


FETCHES = [
    pytest.param(lambda wrapper: wrapper.fetchone(), id='fetchone'),
    pytest.param(lambda wrapper: wrapper.fetchmany(10), id='fetchmany'),
    pytest.param(lambda wrapper: wrapper.fetchall(), id='fetchall'),
]


@pytest.mark.parametrize('fetch', FETCHES)
def test_lock_wait_during_fetch_raises_operational_error(fetch):
    # SELECT ... FOR UPDATE waits for the row lock in SQLFetch, and fails there with SQL0913.
    message = '[HY000] [IBM][System i Access ODBC Driver][DB2 for i5/OS]SQL0913 - Row or object T in use. (-913)'
    wrapper, _ = make_wrapper(fetch_error=pyodbc.Error('HY000', message))
    with pytest.raises(pyodbc.OperationalError) as exc_info:
        fetch(wrapper)
    assert exc_info.value.args == ('HY000', message)


@pytest.mark.parametrize('fetch', FETCHES)
def test_fetch_results_are_unchanged(fetch):
    wrapper, _ = make_wrapper()
    assert fetch(wrapper) in ((1,), [(1,)])


@pytest.mark.parametrize('call', [
    pytest.param(lambda wrapper: wrapper.execute('UPDATE t SET x = %s', (1,)), id='execute'),
    *FETCHES,
])
def test_cancelled_call_raises_operational_error(call):
    wrapper, _ = make_wrapper(error=CANCELLED, fetch_error=CANCELLED)
    with pytest.raises(pyodbc.OperationalError) as exc_info:
        call(wrapper)
    assert exc_info.value.args == CANCELLED.args


@pytest.mark.parametrize('call', [
    pytest.param(lambda wrapper: wrapper.execute('UPDATE t SET x = %s', (1,)), id='execute'),
    pytest.param(lambda wrapper: wrapper.executemany('UPDATE t SET x = %s', [(1,)]), id='executemany'),
    *FETCHES,
])
def test_statement_timeout_cancels_a_call_that_runs_too_long(call):
    wrapper, recording_cursor = make_wrapper(block=True, statement_timeout=0.05)
    recording_cursor.executemany = lambda query, params: recording_cursor.execute(query, params[0])
    started = time.monotonic()
    with pytest.raises(pyodbc.OperationalError) as exc_info:
        call(wrapper)
    assert recording_cursor.cancelled.is_set()
    assert exc_info.value.args[0] == 'HY008'
    assert time.monotonic() - started < 2


def test_statement_timeout_does_not_cancel_a_call_that_finishes_in_time():
    wrapper, recording_cursor = make_wrapper(statement_timeout=0.05)
    wrapper.execute('UPDATE t SET x = %s', (1,))
    wrapper.fetchall()
    time.sleep(0.15)  # longer than the timeout: a timer left running would have cancelled by now
    assert not recording_cursor.cancelled.is_set()


def test_without_statement_timeout_nothing_is_cancelled():
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute('UPDATE t SET x = %s', (1,))
    assert wrapper.statement_timeout is None
    assert not recording_cursor.cancelled.is_set()


@pytest.mark.parametrize('value,expected', [(None, None), (0, None), (5, 5.0), (2.5, 2.5)])
def test_statement_timeout_values(value, expected):
    assert validated_statement_timeout(value) == expected


@pytest.mark.parametrize('value', [-1, '30', True])
def test_invalid_statement_timeout_is_rejected(value):
    with pytest.raises(ImproperlyConfigured, match='statement_timeout'):
        validated_statement_timeout(value)


def test_errors_with_a_specific_class_are_unchanged():
    error = pyodbc.ProgrammingError('42S02', '[42S02] SQL0204 - not found. (-204)')
    wrapper, _ = make_wrapper(error=error)
    with pytest.raises(pyodbc.ProgrammingError) as exc_info:
        wrapper.execute('SELECT "A" FROM t WHERE x = %s', (1,))
    assert exc_info.value is error


@pytest.mark.parametrize('query,params,expected', [
    # '?' and keywords inside string literals, quoted identifiers and comments are not markers or clauses.
    ("SELECT 'a?b' AS \"FROM\", %s AS x FROM t WHERE y = %s", ('A', 'B'),
     ("SELECT 'a?b' AS \"FROM\", 'A' AS x FROM t WHERE y = ?", ['B'])),
    ("SELECT /* ? FROM */ %s AS x FROM t -- trailing ?\n WHERE y = %s", ('A', 'B'),
     ("SELECT /* ? FROM */ 'A' AS x FROM t -- trailing ?\n WHERE y = ?", ['B'])),
    # A column named like a keyword is not the FROM keyword.
    ('SELECT "T"."FROM_DATE", %s AS x FROM t WHERE y = %s', ('A', 'B'),
     ("SELECT \"T\".\"FROM_DATE\", 'A' AS x FROM t WHERE y = ?", ['B'])),
    # Subqueries: the inner select list is inlined, its WHERE stays bound.
    ('SELECT a FROM t WHERE b IN (SELECT %s FROM u WHERE c = %s) AND d = %s', ('A', 'C', 'D'),
     ("SELECT a FROM t WHERE b IN (SELECT 'A' FROM u WHERE c = ?) AND d = ?", ['C', 'D'])),
    ('INSERT INTO t (a, b) VALUES (%s, %s)', ('A', 'B'), ('INSERT INTO t (a, b) VALUES (?, ?)', ['A', 'B'])),
])
def test_select_list_markers_are_found_with_sql_quoting_rules(query, params, expected):
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute(query, params)
    assert recording_cursor.executed == [expected]


def test_large_statements_are_handled():
    # For example, bulk_create of 5,000 rows x 4 columns in one statement.
    rows = ', '.join(['(%s, %s, %s, %s)'] * 5000)
    query = f'SELECT "ID" FROM FINAL TABLE (INSERT INTO "T" ("A", "B", "C", "D") VALUES {rows}) AS "T"'
    wrapper, recording_cursor = make_wrapper()
    wrapper.execute(query, tuple(range(20000)))
    [(sql, params)] = recording_cursor.executed
    assert sql.count('?') == 20000 and params == list(range(20000))
