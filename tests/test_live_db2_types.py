"""
Native Db2 for i column types through the ORM, against a live IBM i system (skipped when not configured).

The table is created with plain DDL, as an existing IBM i table would be, and mapped by the
unmanaged Db2Types model.
"""
import datetime
import decimal
import io

import pytest
from django.core.management import call_command
from django.db import connection

from tests.models import Db2Types

pytestmark = pytest.mark.django_db

COLUMNS = {
    'ID': 'INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY',
    'C_CHAR': 'CHAR(5)',
    'C_VARCHAR': 'VARCHAR(20)',
    'C_CHAR_BIT': 'CHAR(4) FOR BIT DATA',
    'C_VARBINARY': 'VARBINARY(10)',
    'C_BINARY': 'BINARY(3)',
    'C_BLOB': 'BLOB(1M)',
    'C_GRAPHIC': 'GRAPHIC(4) CCSID 1200',
    'C_VARGRAPHIC': 'VARGRAPHIC(10) CCSID 1200',
    'C_NVARCHAR': 'NVARCHAR(10)',
    'C_CLOB': 'CLOB(1M) CCSID 1208',
    'C_DBCLOB': 'DBCLOB(1K) CCSID 1200',
    'C_SMALLINT': 'SMALLINT',
    'C_INTEGER': 'INTEGER',
    'C_BIGINT': 'BIGINT',
    'C_DECIMAL': 'DECIMAL(9, 2)',
    'C_NUMERIC': 'NUMERIC(9, 2)',
    'C_DECFLOAT': 'DECFLOAT(16)',
    'C_REAL': 'REAL',
    'C_DOUBLE': 'DOUBLE',
    'C_DATE': 'DATE',
    'C_TIME': 'TIME',
    'C_TIMESTAMP': 'TIMESTAMP',
    'C_BOOLEAN': 'BOOLEAN',
    'C_ROWID': 'ROWID',
}


@pytest.fixture(scope='module', autouse=True)
def db2_types_table(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            definition = ', '.join(f'{name} {column_type}' for name, column_type in COLUMNS.items())
            cursor.execute(f'CREATE TABLE {Db2Types._meta.db_table} ({definition})')
        yield
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE {Db2Types._meta.db_table}')


VALUES = {
    'c_char': 'ab',
    'c_varchar': 'abc',
    'c_char_bit': b'\x00\xff\x10\xab',
    'c_varbinary': b'\x0a\x0b',
    'c_binary': b'\x01\x02\x03',
    'c_blob': b'\x00\x01' * 1000,
    'c_graphic': 'héé',
    'c_vargraphic': 'ünï €',
    'c_nvarchar': '日本',
    'c_clob': 'clob ' * 2000,
    'c_dbclob': 'dbclob ñ',
    'c_smallint': -32768,
    'c_integer': 2147483647,
    'c_bigint': 9223372036854775807,
    'c_decimal': decimal.Decimal('1234567.89'),
    'c_numeric': decimal.Decimal('-7654321.12'),
    'c_decfloat': decimal.Decimal('123.4567'),
    'c_real': 1.25,
    'c_double': 2.5e300,
    'c_date': datetime.date(2024, 5, 17),
    'c_time': datetime.time(13, 45, 30),
    'c_timestamp': datetime.datetime(2024, 5, 17, 13, 45, 30, 123456, tzinfo=datetime.timezone.utc),
    'c_boolean': True,
}

# What each column reads back as, where that differs from the value written.
READ_BACK = {
    'c_char': 'ab   ',  # CHAR is fixed length: values come back padded with blanks
    'c_graphic': 'héé ',  # GRAPHIC is fixed length too
}


def test_values_round_trip_through_the_orm():
    row = Db2Types.objects.create(**VALUES)
    assert row.pk is not None  # generated identity value returned by the insert
    row = Db2Types.objects.get(pk=row.pk)
    for name, value in VALUES.items():
        actual = getattr(row, name)
        if isinstance(actual, memoryview):
            actual = bytes(actual)
        assert actual == READ_BACK.get(name, value), name


@pytest.mark.parametrize('lookup,value', [
    ('c_varchar', 'abc'),
    ('c_char', 'ab'),  # Db2 compares CHAR values ignoring trailing blanks
    ('c_vargraphic__contains', '€'),
    ('c_nvarchar', '日本'),
    ('c_bigint', 9223372036854775807),
    ('c_decimal__gt', decimal.Decimal('1234567.88')),
    ('c_numeric', decimal.Decimal('-7654321.12')),
    ('c_decfloat', decimal.Decimal('123.4567')),
    ('c_double__gt', 1e300),
    ('c_date__year', 2024),
    ('c_time', datetime.time(13, 45, 30)),
    ('c_timestamp__date', datetime.date(2024, 5, 17)),
    ('c_boolean', True),
    ('c_varbinary', b'\x0a\x0b'),
])
def test_filters_on_native_types(lookup, value):
    Db2Types.objects.create(**VALUES)
    Db2Types.objects.create(c_varchar='other')
    assert Db2Types.objects.filter(**{lookup: value}).count() == 1


def test_nulls_round_trip():
    row = Db2Types.objects.create()
    row = Db2Types.objects.get(pk=row.pk)
    assert all(getattr(row, name) is None for name in VALUES)


def test_inspectdb_maps_native_types_to_fields():
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, Db2Types._meta.db_table)
    field_types = {
        column.name: connection.introspection.get_field_type(column.type_code, column) for column in description
    }
    assert field_types == {
        'id': 'AutoField',
        'c_char': 'CharField', 'c_varchar': 'CharField', 'c_graphic': 'CharField', 'c_vargraphic': 'CharField',
        'c_nvarchar': 'CharField',
        'c_char_bit': 'BinaryField', 'c_varbinary': 'BinaryField', 'c_binary': 'BinaryField',
        'c_blob': 'BinaryField', 'c_rowid': 'BinaryField',
        'c_clob': 'TextField', 'c_dbclob': 'TextField',
        'c_smallint': 'SmallIntegerField', 'c_integer': 'IntegerField', 'c_bigint': 'BigIntegerField',
        'c_decimal': 'DecimalField', 'c_numeric': 'DecimalField', 'c_decfloat': 'DecimalField',
        'c_real': 'FloatField', 'c_double': 'FloatField',
        'c_date': 'DateField', 'c_time': 'TimeField', 'c_timestamp': 'DateTimeField',
        'c_boolean': 'BooleanField',
    }


def test_inspectdb_command_generates_a_model():
    out = io.StringIO()
    call_command('inspectdb', Db2Types._meta.db_table, stdout=out)
    output = out.getvalue()
    assert 'class Db2Types(models.Model):' in output
    assert "c_decimal = models.DecimalField(max_digits=9, decimal_places=2" in output
    assert "c_blob = models.BinaryField(" in output
    assert "c_nvarchar = models.CharField(max_length=10" in output
    assert "db_table = 'DB2_TYPES'" in output
