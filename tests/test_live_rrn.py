"""
Models keyed by relative record number (db_column='RRN()'), against a live IBM i system (skipped
when not configured). The tables are keyless, as legacy physical files often are.
"""
import decimal

import pytest
from django.db import connection
from django.db.models import Max

from tests.models import LegacyLine, LegacyRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(scope='module', autouse=True)
def legacy_tables(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE LEGACY_RECORD (NAME CHAR(10), QTY DECIMAL(5, 0))')
            cursor.execute('CREATE TABLE LEGACY_LINE (ID INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, '
                           'RECORD_RRN INTEGER, NOTE VARCHAR(10))')
        yield
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE LEGACY_LINE')
            cursor.execute('DROP TABLE LEGACY_RECORD')


@pytest.fixture
def records():
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO LEGACY_RECORD VALUES ('first', 1), ('second', 2), ('third', 3)")
        cursor.execute('SELECT RRN(LEGACY_RECORD), NAME FROM LEGACY_RECORD')
        return {name.strip(): rrn for rrn, name in cursor.fetchall()}


def test_read_by_record_number(records):
    first = LegacyRecord.objects.get(pk=records['first'])
    assert first.name.strip() == 'first'
    assert first.pk == records['first'] and isinstance(first.pk, int)
    assert LegacyRecord.objects.filter(pk__in=[records['first'], records['third']]).count() == 2
    assert [r.name.strip() for r in LegacyRecord.objects.order_by('-pk')][0] == 'third'
    assert LegacyRecord.objects.aggregate(top=Max('pk'))['top'] == records['third']


def test_update_targets_the_right_record(records):
    second = LegacyRecord.objects.get(pk=records['second'])
    second.qty = 20
    second.save()
    assert LegacyRecord.objects.filter(pk=records['third']).update(qty=30) == 1
    assert dict((r.name.strip(), r.qty) for r in LegacyRecord.objects.all()) == {
        'first': decimal.Decimal(1), 'second': decimal.Decimal(20), 'third': decimal.Decimal(30)}


def test_delete_by_record_number(records):
    LegacyRecord.objects.get(pk=records['first']).delete()
    LegacyRecord.objects.filter(pk=records['second']).delete()
    assert [r.name.strip() for r in LegacyRecord.objects.all()] == ['third']


def test_created_objects_do_not_get_a_wrong_record_number(records):
    created = LegacyRecord.objects.create(name='new', qty=9)
    assert created.pk is None  # the record number of an inserted row can't be read back
    bulk = LegacyRecord.objects.bulk_create([LegacyRecord(name='b1', qty=1), LegacyRecord(name='b2', qty=1)])
    assert [obj.pk for obj in bulk] == [None, None]
    assert LegacyRecord.objects.get(name='new').qty == 9
    assert LegacyRecord.objects.get(pk=records['first']).qty == 1  # existing rows untouched


def test_relations_to_a_record_number_key(records):
    LegacyLine.objects.create(record_id=records['second'], note='n1')
    line = LegacyLine.objects.select_related('record').get(note='n1')
    assert line.record.name.strip() == 'second'
    assert LegacyLine.objects.filter(record__name='second').count() == 1
    assert [len(r.legacyline_set.all()) for r in LegacyRecord.objects.prefetch_related('legacyline_set')] == [0, 1, 0]
