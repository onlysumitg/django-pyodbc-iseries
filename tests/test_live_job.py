"""
How a connection's server job answers inquiry messages, against a live IBM i system (skipped when not
configured). Only CHGJOB is exercised: it changes the connection's own job. message_replies (ADDRPYLE)
changes the system-wide reply list, so it is covered by the offline tests in test_connection.py only.
"""
import os

import pytest
from django.db import connection

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_SYSTEM_HOST'), reason='TEST_SYSTEM_HOST is not set')

JOB_INQUIRY_REPLY_SQL = (
    "SELECT INQUIRY_MESSAGE_REPLY FROM TABLE(QSYS2.JOB_INFO(JOB_USER_FILTER => '*ALL')) X "
    "WHERE JOB_NAME = QSYS2.JOB_NAME"
)


@pytest.fixture
def new_connection(django_db_setup, django_db_blocker):
    connections_made = []

    def make(**options):
        other = connection.copy()
        other.settings_dict['OPTIONS'] = {**other.settings_dict['OPTIONS'], **options}
        connections_made.append(other)
        return other

    with django_db_blocker.unblock():
        yield make
        for other in connections_made:
            other.close()


def job_inquiry_reply(conn):
    with conn.cursor() as cursor:
        cursor.execute(JOB_INQUIRY_REPLY_SQL)
        return cursor.fetchone()[0].strip()


@pytest.mark.parametrize('value', ['*DFT', '*RQD', '*SYSRPYL'])
def test_inquiry_message_reply_option_sets_the_job(new_connection, value):
    assert job_inquiry_reply(new_connection(inquiry_message_reply=value)) == value


def test_job_setting_is_restored_when_the_connection_closes(new_connection):
    # Server jobs are reused by later connections, so a changed setting must not outlive this connection.
    other = new_connection(inquiry_message_reply='*DFT')
    other.ensure_connection()
    original = other.databaseWrapper.restore_inquiry_message_reply
    if original == '*DFT':
        other.close()
        other = new_connection(inquiry_message_reply='*RQD')
        other.ensure_connection()
        original = other.databaseWrapper.restore_inquiry_message_reply
    with other.cursor() as cursor:
        cursor.execute('SELECT QSYS2.JOB_NAME FROM SYSIBM.SYSDUMMY1')
        job_name = cursor.fetchone()[0].strip()
    assert original in ('*RQD', '*DFT', '*SYSRPYL')
    assert job_inquiry_reply(other) != original

    other.close()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT INQUIRY_MESSAGE_REPLY FROM TABLE(QSYS2.JOB_INFO(JOB_USER_FILTER => '*ALL')) X "
            "WHERE JOB_NAME = %s", [job_name])
        assert cursor.fetchone()[0].strip() == original


def test_schema_editor_uses_the_system_reply_list_only_while_it_runs(new_connection):
    # Data-loss inquiries (CPA32B2) during schema changes are answered from the system reply list.
    other = new_connection(inquiry_message_reply='*DFT')
    assert job_inquiry_reply(other) == '*DFT'
    with other.schema_editor() as editor:
        assert job_inquiry_reply(editor.connection) == '*SYSRPYL'
    assert job_inquiry_reply(other) == '*DFT'
