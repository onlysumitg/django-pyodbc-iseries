"""
Tests for building the IBM i Access ODBC connection, without connecting: pyodbc.connect is replaced
by a stub that records the connection string and keyword arguments it receives.
"""
import pytest
from django.core.exceptions import ImproperlyConfigured

from django_iseries import pybase


class StubCursor:
    def __init__(self, executed, fail_commands):
        self.executed = executed
        self.fail_commands = fail_commands

    def execute(self, sql):
        self.executed.append(sql)
        if self.fail_commands and 'QCMDEXC' in sql:
            raise pybase.Database.Error('HY000', '[HY000] CPF0006 - Errors occurred in command. (-443)')

    def fetchone(self):
        return ('*RQD',)  # the job's inquiry message reply before this connection changes it

    def close(self):
        pass


class StubConnection:
    def __init__(self, fail_commands=False):
        self.executed = []
        self.fail_commands = fail_commands

    def cursor(self):
        return StubCursor(self.executed, self.fail_commands)

    def close(self):
        self.executed.append('CLOSE')


@pytest.fixture
def connect(monkeypatch):
    calls = []
    fail_commands = []

    def fake_connect(connection_string, **kwargs):
        connection = StubConnection(fail_commands=bool(fail_commands))
        calls.append((connection_string, kwargs, connection))
        return connection

    monkeypatch.setattr(pybase.Database, 'connect', fake_connect)
    monkeypatch.setattr(pybase, 'default_driver_name', lambda: 'IBM i Access ODBC Driver')
    monkeypatch.setattr(pybase, '_added_reply_list_entries', set())

    def make_connection(**params):
        params = {'database': 'iseries', 'host': 'myhost', 'user': 'me', 'password': 'pw', **params}
        wrapper = pybase.DatabaseWrapper()
        wrapper.get_new_connection(params)
        make_connection.wrapper = wrapper
        return calls[-1]

    make_connection.fail_commands = lambda: fail_commands.append(True)
    return make_connection


def attributes(connection_string):
    return [attribute for attribute in connection_string.split(';') if attribute]


def test_default_connection_string(connect):
    connection_string, kwargs, _ = connect()
    assert attributes(connection_string) == [
        'DRIVER=IBM i Access ODBC Driver', 'DATABASE=iseries', 'UNICODESQL=1', 'SYSTEM=myhost', 'PROTOCOL=TCPIP',
        'UID=me', 'PWD=pw', 'MAXFIELDLEN=2097152', 'CMT=1', 'CONCURRENTACCESSRESOLUTION=1',
    ]
    assert kwargs == {'autocommit': False}


def test_max_field_length_can_be_overridden(connect):
    connection_string, _, _ = connect(options={'maxfieldlen': 1024})
    assert attributes(connection_string).count('MAXFIELDLEN=1024') == 1
    assert 'MAXFIELDLEN=2097152' not in connection_string


@pytest.mark.parametrize('options,expected', [
    ({'cmt': 2}, ['CMT=2', 'CONCURRENTACCESSRESOLUTION=1']),
    ({'CMT': 0, 'ConcurrentAccessResolution': 2}, ['CMT=0', 'CONCURRENTACCESSRESOLUTION=2']),
])
def test_isolation_defaults_can_be_overridden(connect, options, expected):
    connection_string, _, _ = connect(options=options)
    keywords = [a for a in attributes(connection_string) if a.split('=')[0] in ('CMT', 'CONCURRENTACCESSRESOLUTION')]
    assert keywords == expected


def test_port_is_included_when_set(connect):
    connection_string, _, _ = connect(port='8471')
    assert 'PORT=8471;' in connection_string


def test_values_with_special_characters_are_quoted(connect):
    connection_string, _, _ = connect(password='p;w}d')
    assert 'PWD={p;w}}d};' in connection_string


def test_settings_and_options_map_to_connection_keywords(connect):
    connection_string, _, _ = connect(
        security='SSL', sslclientkeydb='/keys/db.kdb',
        options={'nam': 1, 'cmt': 0, 'trueautocommit': 1, 'dbq': ['LIB1', 'LIB2']},
    )
    for attribute in ('SECURITY=SSL', 'SSLCLIENTKEYSTOREDB=/keys/db.kdb', 'NAM=1', 'CMT=0', 'TRUEAUTOCOMMIT=1',
                      'DBQ=,LIB1,LIB2'):
        assert attribute in attributes(connection_string)


def test_library_list_may_be_a_string(connect):
    connection_string, _, _ = connect(options={'dbq': 'LIB1 LIB2,LIB3'})
    assert 'DBQ=,LIB1,LIB2,LIB3' in attributes(connection_string)


def test_driver_can_be_overridden(connect):
    connection_string, _, _ = connect(options={'driver': 'iSeries Access ODBC Driver'})
    assert attributes(connection_string)[0] == 'DRIVER=iSeries Access ODBC Driver'


def test_extended_dynamic_sql_packages_can_be_enabled_through_options(connect):
    connection_string, _, _ = connect(options={'XDYNAMIC': 1, 'PKG': 'MYLIB/DJANGO,2,0,0,1,512'})
    assert 'XDYNAMIC=1' in attributes(connection_string)
    assert 'PKG=MYLIB/DJANGO,2,0,0,1,512' in attributes(connection_string)


def test_other_options_go_to_pyodbc_or_the_connection_string(connect):
    connection_string, kwargs, _ = connect(options={'timeout': 5, 'BLOCKSIZE': 512})
    assert kwargs == {'autocommit': False, 'timeout': 5}
    assert 'BLOCKSIZE=512' in attributes(connection_string)


def commands(connection):
    return [sql for sql in connection.executed if 'QCMDEXC' in sql]


def test_job_inquiry_message_handling_is_unchanged_by_default(connect):
    _, _, connection = connect(currentschema='MYLIB')
    assert connection.executed == ['SET CURRENT_SCHEMA = MYLIB']


@pytest.mark.parametrize('value', ['*RQD', '*dft', '*SYSRPYL'])
def test_inquiry_message_reply_option_changes_the_job(connect, value):
    connection_string, kwargs, connection = connect(options={'inquiry_message_reply': value})
    assert commands(connection) == [f"{{call QSYS2.QCMDEXC('CHGJOB INQMSGRPY({value.upper()})')}}"]
    assert 'inquiry_message_reply' not in connection_string.lower() and 'inquiry_message_reply' not in kwargs


def test_message_replies_are_added_once_per_process_and_use_the_reply_list(connect):
    connection_string, kwargs, connection = connect(
        currentschema='MYLIB', options={'message_replies': [(9001, 'cpa32b2', 'i')]})
    assert 'message_replies' not in connection_string and 'message_replies' not in kwargs
    assert connection.executed[0] == 'SET CURRENT_SCHEMA = MYLIB'
    assert commands(connection) == [
        "{call QSYS2.QCMDEXC('ADDRPYLE SEQNBR(9001) MSGID(CPA32B2) RPY(I)')}",
        "{call QSYS2.QCMDEXC('CHGJOB INQMSGRPY(*SYSRPYL)')}",
    ]
    _, _, second = connect(options={'message_replies': [(9001, 'CPA32B2', 'I')]})
    assert commands(second) == ["{call QSYS2.QCMDEXC('CHGJOB INQMSGRPY(*SYSRPYL)')}"]


@pytest.mark.parametrize('options', [
    {'inquiry_message_reply': '*SYSRPYL) DLTLIB(MYLIB'},
    {'message_replies': [(9001, 'CPA32B2', "I') DLTLIB(MYLIB")]},
    {'message_replies': [(0, 'CPA32B2', 'I')]},
    {'message_replies': [(9001, 'NOTANID', 'I')]},
    {'message_replies': [(9001, 'CPA32B2')]},
])
def test_invalid_values_are_rejected_before_running_commands(connect, options):
    with pytest.raises(ImproperlyConfigured):
        connect(options=options)


def test_failed_commands_are_logged_and_the_connection_is_still_used(connect, caplog):
    connect.fail_commands()
    with caplog.at_level('WARNING', logger='django_iseries'):
        _, _, connection = connect(options={'message_replies': [(9001, 'CPA32B2', 'I')]})
    assert len(commands(connection)) == 2
    assert [record.getMessage().split(' failed')[0] for record in caplog.records] == [
        'IBM i command ADDRPYLE SEQNBR(9001) MSGID(CPA32B2) RPY(I)',
        'IBM i command CHGJOB INQMSGRPY(*SYSRPYL)',
    ]
    # A failed entry isn't remembered as added, so the next connection tries again.
    assert pybase._added_reply_list_entries == set()


def test_changed_job_setting_is_restored_when_the_connection_closes(connect):
    _, _, connection = connect(options={'inquiry_message_reply': '*DFT'})
    assert any('JOB_INFO' in sql for sql in connection.executed)  # the job's setting was read first
    connect.wrapper.close(connection)
    assert commands(connection)[-1] == "{call QSYS2.QCMDEXC('CHGJOB INQMSGRPY(*RQD)')}"
    assert connection.executed[-1] == 'CLOSE'


def test_nothing_is_restored_when_the_job_was_not_changed(connect):
    _, _, connection = connect()
    connect.wrapper.close(connection)
    assert connection.executed == ['CLOSE']


def test_statement_timeout_is_not_sent_to_the_driver_and_reaches_cursors(connect):
    connection_string, kwargs, connection = connect(options={'statement_timeout': 30})
    assert 'statement_timeout' not in connection_string.lower()
    assert 'statement_timeout' not in kwargs
    cursor = connect.wrapper._cursor(connection)
    assert cursor.statement_timeout == 30.0


def test_statement_timeout_defaults_to_off(connect):
    _, _, connection = connect()
    assert connect.wrapper._cursor(connection).statement_timeout is None


def test_invalid_statement_timeout_is_rejected_when_connecting(connect):
    with pytest.raises(ImproperlyConfigured, match='statement_timeout'):
        connect(options={'statement_timeout': 'soon'})
