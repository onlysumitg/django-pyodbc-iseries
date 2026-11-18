"""
Schema changes (what migrations do) against a live IBM i system (skipped when not configured).

Migrations aren't run in a transaction on Db2 for i (see can_rollback_ddl), so these tests don't
run in one either; each drops the tables it creates.
"""
import pytest
from django.db import NotSupportedError, connection, models
from django.db.models import CheckConstraint, Index, Q, UniqueConstraint
from django.test.utils import isolate_apps

from django_iseries.fields import NVarCharField
from tests.models import Person

pytestmark = pytest.mark.django_db(transaction=True)


def table_columns(table):
    with connection.cursor() as cursor:
        return {column.name: column for column in connection.introspection.get_table_description(cursor, table)}


def table_constraints(table):
    with connection.cursor() as cursor:
        return connection.introspection.get_constraints(cursor, table)


def field(model, name):
    return model._meta.get_field(name)


@pytest.fixture
def temp_model():
    with isolate_apps('tests'):
        class SchemaTemp(models.Model):
            name = models.CharField(max_length=20)
            qty = models.IntegerField(default=0)

            class Meta:
                app_label = 'tests'
                db_table = 'schema_temp'

        with connection.schema_editor() as editor:
            editor.create_model(SchemaTemp)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO SCHEMA_TEMP (NAME, QTY) VALUES ('existing', 1)")
        yield SchemaTemp
        drop_tables('schema_temp', 'schema_temp_renamed', 'schema_keyed')


def drop_tables(*tables):
    with connection.cursor() as cursor:
        existing = connection.introspection.table_names(cursor)
        for table in tables:
            if table in existing:
                cursor.execute(f'DROP TABLE {connection.ops.quote_name(table)}')


def add_field(model, name, new_field):
    new_field.set_attributes_from_name(name)
    new_field.model = model
    with connection.schema_editor() as editor:
        editor.add_field(model, new_field)
    return new_field


def alter_field(model, old_field, new_field):
    new_field.set_attributes_from_name(old_field.name)
    new_field.model = model
    with connection.schema_editor() as editor:
        editor.alter_field(model, old_field, new_field, strict=True)
    return new_field


def test_create_and_delete_model(temp_model):
    columns = table_columns('schema_temp')
    assert set(columns) == {'id', 'name', 'qty'}
    with connection.cursor() as cursor:
        assert 'schema_temp' in connection.introspection.table_names(cursor)
    with connection.schema_editor() as editor:
        editor.delete_model(temp_model)
    with connection.cursor() as cursor:
        assert 'schema_temp' not in connection.introspection.table_names(cursor)


def test_add_nullable_and_not_null_columns(temp_model):
    add_field(temp_model, 'note', models.CharField(max_length=10, null=True))
    add_field(temp_model, 'status', models.CharField(max_length=10, default='new'))
    columns = table_columns('schema_temp')
    assert columns['note'].null_ok and not columns['status'].null_ok
    with connection.cursor() as cursor:
        cursor.execute("SELECT STATUS FROM SCHEMA_TEMP")
        assert cursor.fetchall() == [('new',)]  # existing row received the default


def test_alter_column_type_length_and_nullability(temp_model):
    name = alter_field(temp_model, field(temp_model, 'name'), models.CharField(max_length=50, null=True))
    assert table_columns('schema_temp')['name'].display_size == 50
    assert table_columns('schema_temp')['name'].null_ok
    alter_field(temp_model, name, models.CharField(max_length=50))
    assert not table_columns('schema_temp')['name'].null_ok
    alter_field(temp_model, field(temp_model, 'qty'), models.BigIntegerField(default=0))
    assert table_columns('schema_temp')['qty'].type_code == 'BIGINT'


def test_alter_char_to_text(temp_model):
    alter_field(temp_model, field(temp_model, 'name'), models.TextField())
    assert table_columns('schema_temp')['name'].type_code == 'CLOB'
    with connection.cursor() as cursor:
        cursor.execute("SELECT NAME FROM SCHEMA_TEMP")
        assert cursor.fetchall() == [('existing',)]


def test_add_and_remove_index_and_unique(temp_model):
    name = alter_field(temp_model, field(temp_model, 'name'), models.CharField(max_length=20, db_index=True))
    assert any(c['index'] and c['columns'] == ['name'] for c in table_constraints('schema_temp').values())
    name = alter_field(temp_model, name, models.CharField(max_length=20, unique=True))
    assert any(c['unique'] and c['columns'] == ['name'] for c in table_constraints('schema_temp').values())
    alter_field(temp_model, name, models.CharField(max_length=20))
    assert not any(c['columns'] == ['name'] for c in table_constraints('schema_temp').values())


def test_add_remove_model_indexes_and_constraints(temp_model):
    index = Index(fields=['name', '-qty'], name='schema_temp_name_qty')
    unique = UniqueConstraint(fields=['name', 'qty'], name='schema_temp_name_qty_uniq')
    check = CheckConstraint(condition=Q(qty__gte=0), name='schema_temp_qty_gte_0')
    with connection.schema_editor() as editor:
        editor.add_index(temp_model, index)
        editor.add_constraint(temp_model, unique)
        editor.add_constraint(temp_model, check)
    constraints = table_constraints('schema_temp')
    assert constraints['schema_temp_name_qty']['orders'] == ['ASC', 'DESC']
    assert constraints['schema_temp_name_qty_uniq']['unique']
    assert constraints['schema_temp_qty_gte_0']['check']
    with connection.schema_editor() as editor:
        editor.remove_index(temp_model, index)
        editor.remove_constraint(temp_model, unique)
        editor.remove_constraint(temp_model, check)
    assert not {'schema_temp_name_qty', 'schema_temp_name_qty_uniq', 'schema_temp_qty_gte_0'} & set(
        table_constraints('schema_temp'))


def test_add_and_remove_foreign_key(temp_model):
    owner = add_field(temp_model, 'owner', models.ForeignKey(Person, models.CASCADE, null=True))
    assert any(c['foreign_key'] == ('tests_person', 'id') for c in table_constraints('schema_temp').values())
    with connection.schema_editor() as editor:
        editor.remove_field(temp_model, owner)
    assert 'owner_id' not in table_columns('schema_temp')


def test_integer_to_auto_field_and_back(temp_model):
    with isolate_apps('tests'):
        class SchemaKeyed(models.Model):
            key = models.IntegerField(primary_key=True)

            class Meta:
                app_label = 'tests'
                db_table = 'schema_keyed'

        with connection.schema_editor() as editor:
            editor.create_model(SchemaKeyed)
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO SCHEMA_KEYED (KEY) VALUES (41)')
        key = alter_field(SchemaKeyed, field(SchemaKeyed, 'key'), models.AutoField(primary_key=True))
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO SCHEMA_KEYED VALUES (DEFAULT)')
            cursor.execute('SELECT MAX(KEY) FROM SCHEMA_KEYED')
            assert cursor.fetchone() == (42,)  # the identity continues after the existing values
        alter_field(SchemaKeyed, key, models.IntegerField(primary_key=True))
        with connection.cursor() as cursor:
            cursor.execute('SELECT IS_IDENTITY FROM QSYS2.SYSCOLUMNS WHERE TABLE_SCHEMA = CURRENT_SCHEMA '
                           "AND TABLE_NAME = 'SCHEMA_KEYED' AND COLUMN_NAME = 'KEY'")
            assert cursor.fetchone() == ('NO',)


def test_rename_table(temp_model):
    with connection.schema_editor() as editor:
        editor.alter_db_table(temp_model, 'schema_temp', 'schema_temp_renamed')
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
    assert 'schema_temp_renamed' in tables and 'schema_temp' not in tables


def test_rename_column_is_rejected_with_guidance(temp_model):
    new_field = models.CharField(max_length=20)
    new_field.set_attributes_from_name('title')
    new_field.model = temp_model
    with pytest.raises(NotSupportedError, match="db_column='name'"):
        with connection.schema_editor() as editor:
            editor.alter_field(temp_model, field(temp_model, 'name'), new_field)


def test_convert_char_field_to_nvarchar_field(temp_model):
    alter_field(temp_model, field(temp_model, 'name'), NVarCharField(max_length=20))
    assert table_columns('schema_temp')['name'].type_code == 'VARG'
    with connection.cursor() as cursor:
        cursor.execute("SELECT NAME FROM SCHEMA_TEMP")
        assert cursor.fetchall() == [('existing',)]
        cursor.execute("INSERT INTO SCHEMA_TEMP (NAME, QTY) VALUES (%s, 1)", ['€' * 20])
