"""
Regression tests for backend hooks whose signatures or behavior no longer match the installed Django.

Querysets are only compiled to SQL, never executed, so no IBM i connection is needed (tests are not
marked django_db, so pytest-django blocks any accidental database access).
"""
import pytest
from django.core.management.color import no_style
from django.db import connection, models
from django.db.models.functions import ExtractMonth, TruncMonth
from django.db.utils import NotSupportedError
from django.utils import timezone

from tests.models import Item


def compile_sql(queryset):
    return queryset.query.get_compiler(using=queryset.db).as_sql()


def test_plain_filter_compiles():
    sql, params = compile_sql(Item.objects.filter(name='a'))
    assert 'WHERE' in sql
    assert params == ('a',)


@pytest.mark.parametrize('build_queryset', [
    pytest.param(lambda: Item.objects.filter(date__month=5), id='date__month'),
    pytest.param(lambda: Item.objects.annotate(m=ExtractMonth('date')), id='ExtractMonth'),
    pytest.param(lambda: Item.objects.filter(last_modified__date='2024-01-01'), id='datetime__date'),
    pytest.param(lambda: Item.objects.annotate(m=TruncMonth('date')), id='TruncMonth-date'),
    pytest.param(lambda: Item.objects.annotate(m=TruncMonth('last_modified')), id='TruncMonth-datetime'),
])
def test_date_lookups_and_functions_compile(build_queryset):
    compile_sql(build_queryset())


def test_date_year_lookup_compiles():
    compile_sql(Item.objects.filter(date__year=2024))


def test_sql_flush_accepts_django_keyword_arguments():
    # An empty table list keeps this offline; Django always passes these as keywords.
    assert connection.ops.sql_flush(no_style(), [], reset_sequences=True, allow_cascade=False) == []


def test_constraint_checks_disabled_context_is_usable():
    # loaddata wraps every fixture load in this context manager.
    with connection.constraint_checks_disabled():
        pass


def test_union_compiles():
    compile_sql(Item.objects.filter(name='a').union(Item.objects.filter(name='b')))


def test_datetime_functions_reject_time_zone_other_than_storage_time_zone():
    # Db2 for i has no named time zone conversion, so evaluating in another zone must fail loudly.
    with timezone.override('America/Chicago'):
        with pytest.raises(NotSupportedError, match='America/Chicago'):
            compile_sql(Item.objects.annotate(m=TruncMonth('last_modified')))


def test_offset_without_limit_uses_rows_keyword():
    sql, _ = compile_sql(Item.objects.all()[10:])
    assert sql.endswith('OFFSET 10 ROWS')


def test_insert_without_returning_fields_is_a_plain_insert():
    from django.db.models.sql import InsertQuery

    query = InsertQuery(Item)
    query.insert_values([Item._meta.get_field('name')], [Item(name='a')])
    [(sql, params)] = query.get_compiler(using='default').as_sql()
    assert sql.startswith('INSERT INTO')
    assert params == ('a',)


def test_rrn_primary_key_is_rendered_as_rrn_function_for_every_alias():
    from tests.models import LegacyLine

    sql, _ = compile_sql(LegacyLine.objects.select_related('record').filter(record__name='a', record__pk__gt=1))
    assert '"RRN()"' not in sql
    assert 'RRN("LEGACY_RECORD")' in sql


def test_failed_flush_re_adds_dropped_foreign_keys(monkeypatch):
    from django.db.backends.base.operations import BaseDatabaseOperations

    add_fk = 'ALTER TABLE "CHILD" ADD CONSTRAINT "FK1" FOREIGN KEY ("P") REFERENCES "PARENT" ("ID")'
    sql_list = ['ALTER TABLE "CHILD" DROP CONSTRAINT "FK1"', 'COMMIT', 'DELETE FROM "PARENT"', add_fk]
    executed = []

    def failing_flush(self, statements):
        raise RuntimeError('flush failed after dropping the foreign key')

    class RecordingCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql):
            executed.append(sql)

    monkeypatch.setattr(BaseDatabaseOperations, 'execute_sql_flush', failing_flush)
    monkeypatch.setattr(connection, 'cursor', RecordingCursor)
    with pytest.raises(RuntimeError):
        connection.ops.execute_sql_flush(sql_list)
    assert executed == [add_fk]


def test_random_ordering_uses_rand():
    sql, _ = compile_sql(Item.objects.order_by('?'))
    assert 'RAND()' in sql and 'RANDOM()' not in sql


def test_releases_older_than_7_3_are_rejected(monkeypatch):
    from django.db import connections

    default = connections['default']
    monkeypatch.setattr(default, 'get_database_version', lambda: (7, 2))
    with pytest.raises(NotSupportedError, match='7.3 or later is required'):
        default.check_database_version_supported()


def test_nvarchar_field_column_type():
    from django_iseries.fields import NVarCharField
    from tests.models import UnicodeName

    field = UnicodeName._meta.get_field('name')
    assert field.db_type(connection) == 'NVARCHAR(20)'
    assert field.cast_db_type(connection) == 'NVARCHAR(20)'
    with connection.schema_editor(collect_sql=True) as editor:
        editor.create_model(UnicodeName)
    assert '"NAME" NVARCHAR(20) NOT NULL' in editor.collected_sql[0]
    name, path, args, kwargs = NVarCharField(max_length=5).deconstruct()
    assert path == 'django_iseries.fields.NVarCharField' and kwargs == {'max_length': 5}


def test_nvarchar_field_is_a_plain_char_field_elsewhere():
    from django.db import connections
    from django_iseries.fields import NVarCharField

    sqlite = connections['other']
    assert NVarCharField(max_length=5).db_type(sqlite) == models.CharField(max_length=5).db_type(sqlite)


def test_bulk_batches_stay_below_the_parameter_limit():
    fields = [Item._meta.get_field(name) for name in ('name', 'date', 'time', 'last_modified')]
    assert connection.ops.bulk_batch_size(fields, [None] * 50000) == 2500
    assert connection.ops.bulk_batch_size([], [None] * 7) == 7
    # bulk_update passes [pk, pk, *fields]: one CASE branch per object, so batches are kept small.
    pk = Item._meta.pk
    assert connection.ops.bulk_batch_size([pk, pk, fields[0]], [None] * 50000) == 250
