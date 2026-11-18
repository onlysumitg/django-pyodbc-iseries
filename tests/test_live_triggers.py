"""
Inserts into a table with an AFTER INSERT trigger, against a live IBM i system (skipped when not configured).

When such a trigger changes the inserted row, SELECT ... FROM FINAL TABLE (INSERT ...) waits out the
record lock timeout and fails (SQL0723), so the backend uses plain INSERTs for these tables.
"""
import pytest
from django.db import connection

from tests.models import TriggeredItem

pytestmark = [pytest.mark.django_db, pytest.mark.timeout(30)]

TRIGGER = 'TESTS_TRIGGEREDITEM_AI'


@pytest.fixture(scope='module', autouse=True)
def after_insert_trigger(django_db_setup, django_db_blocker):
    table = connection.ops.quote_name(TriggeredItem._meta.db_table)
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TRIGGER {TRIGGER} AFTER INSERT ON {table} REFERENCING NEW AS N FOR EACH ROW "
                f"UPDATE {table} SET STAMP = 'touched' WHERE ID = N.ID"
            )
        yield
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TRIGGER {TRIGGER}')


def test_trigger_is_detected():
    assert connection.ops.has_after_insert_triggers(TriggeredItem._meta.db_table)


def test_create_returns_primary_key_and_trigger_runs():
    item = TriggeredItem.objects.create(name='a')
    assert isinstance(item.pk, int)
    assert TriggeredItem.objects.get(pk=item.pk).stamp == 'touched'


def test_bulk_create_returns_primary_keys_in_order():
    items = TriggeredItem.objects.bulk_create([TriggeredItem(name=name) for name in ('c', 'a', 'b')])
    names = dict(TriggeredItem.objects.values_list('pk', 'name'))
    assert [names[item.pk] for item in items] == ['c', 'a', 'b']
    assert set(TriggeredItem.objects.values_list('stamp', flat=True)) == {'touched'}
