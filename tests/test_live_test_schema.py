"""
Test schema isolation (what manage.py test does), against a live IBM i system (skipped when not configured).

Runs on a separate connection with its own short-lived schema, so the suite's own test schema is untouched.
"""
import os
import secrets

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, connections

# Not django_db: this uses its own connection outside pytest-django's per-test transaction.
pytestmark = pytest.mark.skipif(not os.environ.get('TEST_SYSTEM_HOST'), reason='TEST_SYSTEM_HOST is not set')

ALIAS = 'schema_isolation_test'


def schema_exists(schema):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM QSYS2.SYSSCHEMAS WHERE SCHEMA_NAME = %s', [schema])
        return cursor.fetchone() is not None


@pytest.fixture
def isolated(monkeypatch, django_db_setup, django_db_blocker):
    django_db_blocker.unblock()
    other = connection.copy(ALIAS)
    test_schema = 'DJT' + secrets.token_hex(3).upper()
    other.settings_dict['CURRENTSCHEMA'] = os.environ['TEST_SYSTEM_SCHEMA']
    other.settings_dict['TEST'] = {**other.settings_dict['TEST'], 'SCHEMA': test_schema, 'CREATE_DB': True}
    monkeypatch.setitem(settings.DATABASES, ALIAS, other.settings_dict)
    yield other, test_schema
    other.close()
    # migrate looks the alias up in Django's connection registry; don't leave it pointing at this schema.
    if hasattr(connections._connections, ALIAS):
        connections[ALIAS].close()
        del connections[ALIAS]
    connections.settings.pop(ALIAS, None)
    if schema_exists(test_schema):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA {test_schema} CASCADE')
    django_db_blocker.restore()


def current_schema(conn):
    with conn.cursor() as cursor:
        cursor.execute('SELECT CURRENT_SCHEMA FROM SYSIBM.SYSDUMMY1')
        return cursor.fetchone()[0].strip()


def test_tests_run_in_a_created_schema_that_is_dropped_afterwards(isolated):
    other, test_schema = isolated
    primary = other.settings_dict['CURRENTSCHEMA']

    other.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        assert schema_exists(test_schema)
        assert current_schema(other) == test_schema
        with other.cursor() as cursor:
            # Writable under commitment control: CREATE SCHEMA journals its tables.
            cursor.execute('CREATE TABLE PROBE (ID INTEGER)')
            cursor.execute('INSERT INTO PROBE VALUES (1)')
    finally:
        other.creation.destroy_test_db(other.settings_dict['NAME'], verbosity=0, keepdb=False)

    assert not schema_exists(test_schema)
    assert other.settings_dict['CURRENTSCHEMA'] == primary
    assert settings.DATABASES[ALIAS]['CURRENTSCHEMA'] == primary
    assert current_schema(other) == primary.upper()


def test_keepdb_reuses_and_preserves_the_schema(isolated):
    other, test_schema = isolated
    other.creation.create_test_db(verbosity=0, keepdb=True)
    with other.cursor() as cursor:
        cursor.execute('CREATE TABLE KEPT (ID INTEGER)')
    other.creation.destroy_test_db(other.settings_dict['NAME'], verbosity=0, keepdb=True)
    assert schema_exists(test_schema)

    other.creation.create_test_db(verbosity=0, keepdb=True)
    try:
        with other.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM KEPT')
            assert cursor.fetchone() == (0,)
    finally:
        other.creation.destroy_test_db(other.settings_dict['NAME'], verbosity=0, keepdb=True)


def test_missing_schema_is_an_error_when_creation_is_disabled(isolated):
    other, test_schema = isolated
    other.settings_dict['TEST']['CREATE_DB'] = False
    with pytest.raises(ImproperlyConfigured, match='does not exist'):
        other.creation.create_test_db(verbosity=0)
    assert not schema_exists(test_schema)
