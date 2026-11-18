"""
Transaction isolation against a live IBM i system (skipped when not configured): readers don't see other
transactions' uncommitted changes, and don't wait for them either (CMT=1 with currently committed).
Writers do wait for row locks; OPTIONS['statement_timeout'] bounds that wait.
"""
import os
import time

import pytest
from django.db import OperationalError, connection

pytestmark = [
    pytest.mark.skipif(not os.environ.get('TEST_SYSTEM_HOST'), reason='TEST_SYSTEM_HOST is not set'),
    pytest.mark.timeout(30),
]


@pytest.fixture
def make_connection(django_db_setup, django_db_blocker):
    made = []

    def make(**options):
        other = connection.copy()
        other.settings_dict['OPTIONS'] = {**other.settings_dict['OPTIONS'], **options}
        made.append(other)
        return other

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM TESTS_PERSON WHERE FIRST_NAME LIKE 'iso-%'")
            cursor.execute("INSERT INTO TESTS_PERSON (FIRST_NAME, LAST_NAME) VALUES ('iso-row', 'committed')")
        yield make
        for other in made:
            if other.connection is not None and not other.get_autocommit():
                other.rollback()
            other.close()
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM TESTS_PERSON WHERE FIRST_NAME LIKE 'iso-%'")


def change_without_committing(writer):
    writer.set_autocommit(False)
    with writer.cursor() as cursor:
        cursor.execute("UPDATE TESTS_PERSON SET LAST_NAME = 'uncommitted' WHERE FIRST_NAME = 'iso-row'")
        cursor.execute("INSERT INTO TESTS_PERSON (FIRST_NAME, LAST_NAME) VALUES ('iso-new', 'uncommitted')")


def read(reader):
    started = time.monotonic()
    with reader.cursor() as cursor:
        cursor.execute("SELECT FIRST_NAME, LAST_NAME FROM TESTS_PERSON WHERE FIRST_NAME LIKE 'iso-%' ORDER BY 1")
        return cursor.fetchall(), time.monotonic() - started


def test_reads_in_a_transaction_see_only_committed_data_without_waiting(make_connection):
    change_without_committing(make_connection())
    reader = make_connection()
    reader.set_autocommit(False)
    rows, elapsed = read(reader)
    assert rows == [('iso-row', 'committed')]
    assert elapsed < 5


def test_autocommit_reads_see_only_committed_data_with_trueautocommit(make_connection):
    change_without_committing(make_connection(trueautocommit=1))
    rows, elapsed = read(make_connection(trueautocommit=1))
    assert rows == [('iso-row', 'committed')]
    assert elapsed < 5


def test_committed_changes_are_visible(make_connection):
    writer = make_connection()
    change_without_committing(writer)
    writer.commit()
    rows, _ = read(make_connection())
    assert rows == [('iso-new', 'uncommitted'), ('iso-row', 'uncommitted')]


# Lock waits. The ODBC query timeout doesn't apply to them (they last the file's WAITRCD, 60 s by default),
# so statement_timeout cancels the call instead.

LOCKED_STATEMENTS = [
    pytest.param("UPDATE TESTS_PERSON SET LAST_NAME = 'waiter' WHERE FIRST_NAME = 'iso-row'", id='update'),
    # Waits for the row lock while fetching, not while executing.
    pytest.param("SELECT LAST_NAME FROM TESTS_PERSON WHERE FIRST_NAME = 'iso-row' FOR UPDATE", id='select-for-update'),
]


@pytest.mark.parametrize('sql', LOCKED_STATEMENTS)
@pytest.mark.parametrize('autocommit', [False, True], ids=['transaction', 'autocommit'])
def test_statement_timeout_ends_a_lock_wait(make_connection, sql, autocommit):
    change_without_committing(make_connection())
    waiter = make_connection(statement_timeout=2)
    waiter.set_autocommit(autocommit)
    started = time.monotonic()
    with pytest.raises(OperationalError) as exc_info:
        with waiter.cursor() as cursor:
            cursor.execute(sql)
            cursor.fetchall()
    assert exc_info.value.args[0] == 'HY008'
    assert time.monotonic() - started < 10
    # The connection stays usable after the cancelled call.
    if not autocommit:
        waiter.rollback()
    with waiter.cursor() as cursor:
        cursor.execute('SELECT 1 FROM SYSIBM.SYSDUMMY1')
        assert cursor.fetchall() == [(1,)]


def test_statement_timeout_leaves_unlocked_rows_alone(make_connection):
    change_without_committing(make_connection())
    waiter = make_connection(statement_timeout=2)
    with waiter.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM TESTS_PERSON WHERE FIRST_NAME = 'iso-row'")
        assert cursor.fetchall() == [(1,)]
