# +--------------------------------------------------------------------------+
# |  Licensed Materials - Property of IBM                                    |
# |                                                                          |
# | (C) Copyright IBM Corporation 2009-2018.                                      |
# +--------------------------------------------------------------------------+
# | This module complies with Django 1.0 and is                              |
# | Licensed under the Apache License, Version 2.0 (the "License");          |
# | you may not use this file except in compliance with the License.         |
# | You may obtain a copy of the License at                                  |
# | http://www.apache.org/licenses/LICENSE-2.0 Unless required by applicable |
# | law or agreed to in writing, software distributed under the License is   |
# | distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY |
# | KIND, either express or implied. See the License for the specific        |
# | language governing permissions and limitations under the License.        |
# +--------------------------------------------------------------------------+
# | Authors: Ambrish Bhargava, Tarun Pasrija, Rahul Priyadarshi,             |
# | Hemlata Bhatt, Vyshakh A                                                 |
# +--------------------------------------------------------------------------+

import decimal
import uuid
from collections import namedtuple

from django.conf import settings
from django.db import NotSupportedError, utils
from django.db.backends.base.operations import BaseDatabaseOperations
from django.db.models.fields import AutoFieldMixin
from django.utils import timezone

# Django lookup name -> Db2 for i function returning that part of a date or timestamp.
# DAYOFWEEK counts Sunday as 1, which is what Django's week_day lookup expects.
EXTRACT_FUNCTIONS = {
    'year': 'YEAR',
    'quarter': 'QUARTER',
    'month': 'MONTH',
    'week': 'WEEK_ISO',
    'day': 'DAY',
    'week_day': 'DAYOFWEEK',
    'iso_week_day': 'DAYOFWEEK_ISO',
    'hour': 'HOUR',
    'minute': 'MINUTE',
    'second': 'SECOND',
}

# Django truncation kind -> TRUNC_TIMESTAMP format ('IW' truncates to the ISO week's Monday).
TRUNC_FORMATS = {
    'year': 'YYYY',
    'quarter': 'Q',
    'month': 'MM',
    'week': 'IW',
    'day': 'DD',
    'hour': 'HH',
    'minute': 'MI',
    'second': 'SS',
}

# Objects per UPDATE in bulk_update(): measured fastest on IBM i 7.5, and far below the CASE size limit.
BULK_UPDATE_BATCH_SIZE = 250

ForeignKeyConstraint = namedtuple(
    'ForeignKeyConstraint',
    'referencing_table name referencing_columns referenced_table referenced_columns delete_rule update_rule',
)


class DatabaseOperations(BaseDatabaseOperations):
    compiler_module = "django_iseries.compiler"

    # CAST targets for fields whose creation type can't be used in a CAST (identity clauses).
    cast_data_types = {
        'AutoField': 'INTEGER',
        'BigAutoField': 'BIGINT',
        'SmallAutoField': 'SMALLINT',
    }
    cast_char_field_without_max_length = 'VARCHAR(32000) CCSID 1208'

    def __init__(self, connection):
        super().__init__(connection)
        self._after_insert_trigger_cache = {}

    def get_db_converters(self, expression):
        converters = super().get_db_converters(expression)
        internal_type = expression.output_field.get_internal_type()
        if internal_type in ('AutoField', 'BigAutoField', 'SmallAutoField'):
            converters.append(self.convert_autofield_value)
        elif internal_type == 'BooleanField':
            converters.append(self.convert_booleanfield_value)
        elif internal_type == 'UUIDField':
            converters.append(self.convert_uuidfield_value)
        elif internal_type == 'DecimalField':
            converters.append(self.convert_decimalfield_value)
        elif internal_type == 'FloatField':
            converters.append(self.convert_floatfield_value)
        elif internal_type == 'DateTimeField' and settings.USE_TZ:
            converters.append(self.convert_datetimefield_value)
        return converters

    def convert_autofield_value(self, value, expression, connection):
        # Record numbers (RRN()) are returned as DECIMAL.
        if isinstance(value, decimal.Decimal):
            return int(value)
        return value

    def convert_booleanfield_value(self, value, expression, connection):
        # Booleans are stored as SMALLINT 0/1.
        if value in (0, 1):
            return bool(value)
        return value

    def convert_decimalfield_value(self, value, expression, connection):
        # pyodbc returns DECFLOAT columns as strings.
        if isinstance(value, str):
            return decimal.Decimal(value)
        return value

    def convert_floatfield_value(self, value, expression, connection):
        # pyodbc returns DECFLOAT columns as strings.
        if isinstance(value, str):
            return float(value)
        return value

    def convert_uuidfield_value(self, value, expression, connection):
        if value is not None:
            value = uuid.UUID(value)
        return value

    def convert_datetimefield_value(self, value, expression, connection):
        # Timestamps are stored naive, in the connection's time zone (see adapt_datetimefield_value).
        if value is not None:
            value = timezone.make_aware(value, self.connection.timezone)
        return value

    def combine_duration_expression(self, connector, sub_expressions):
        if 'NULL' in sub_expressions:
            # django expects that adding a null duration will return null
            # db2 does not allow untyped columns
            return 'CAST(NULL AS TIMESTAMP)'

        lhs, rhs = sub_expressions
        if connector not in '+-':
            raise utils.DatabaseError('Invalid connector for timedelta: %s.' % connector)
        if 'MICROSECONDS' not in lhs:
            lhs = f'CAST({lhs} as TIMESTAMP)'
        if 'MICROSECONDS' not in rhs:
            rhs = f'CAST({rhs} as TIMESTAMP)'
            lhs, rhs = rhs, lhs
        if connector == '-' and 'MICROSECONDS' in rhs:
            rhs = rhs.replace('+', '-')
        return f'{lhs} {connector} {rhs}'

    def combine_expression(self, connector, sub_expressions):
        lhs, rhs = sub_expressions
        if connector == '%%':
            # Db2 for iSeries requires explicit cast for parameters to MOD. INTEGER is the only cast that makes sense
            # (though this may cause failures for BIGINT types down the road)
            return f'MOD(CAST({lhs} AS INTEGER), CAST({rhs} AS INTEGER))'
        elif connector == '&':
            return f'BITAND({lhs}, {rhs})'
        elif connector == '|':
            return f'BITOR({lhs}, {rhs})'
        elif connector == '<<':
            return f'({lhs} * POWER(2, CAST({rhs} AS INTEGER)))'
        elif connector == '>>':
            return f'FLOOR({lhs} / POWER(2, CAST({rhs} AS INTEGER)))'
        elif connector == '^':
            # Db2 for iSeries requires explicit cast for parameters to POWER. FLOAT is chosen here as it will return
            # correct values, though it may sacrifice a small amount of speed
            return f'POWER(CAST({lhs} AS FLOAT), CAST({rhs} AS FLOAT))'
        return super().combine_expression(connector, sub_expressions)

    def format_for_duration_arithmetic(self, sql):
        if sql == 'NULL':
            return sql
        return 'CAST(%s AS BIGINT) MICROSECONDS' % sql

    def date_interval_sql(self, timedelta):
        return "%d DAYS + %d SECONDS + %d MICROSECONDS" % (
            timedelta.days, timedelta.seconds, timedelta.microseconds)

    # Date and time functions. Each takes the column SQL and its params and returns (sql, params).

    def _convert_sql_to_tz(self, sql, params, tzname):
        """
        Db2 for i has no function to convert between named time zones, so timestamps can only be
        evaluated in the time zone they are stored in (the connection's time zone).
        """
        if tzname and settings.USE_TZ and tzname != self.connection.timezone_name:
            raise NotSupportedError(
                f"Db2 for i cannot convert timestamps to time zone {tzname!r} in SQL. Stored timestamps are in "
                f"{self.connection.timezone_name!r}; set DATABASES[...]['TIME_ZONE'] (or activate that time zone) "
                f"so the two match."
            )
        return sql, params

    def date_extract_sql(self, lookup_type, sql, params):
        lookup_type = lookup_type.lower()
        if lookup_type == 'iso_year':
            # The ISO year is the year of the Thursday in the same ISO week.
            return f"YEAR({sql} + (4 - DAYOFWEEK_ISO({sql})) DAYS)", (*params, *params)
        try:
            function = EXTRACT_FUNCTIONS[lookup_type]
        except KeyError:
            raise ValueError(f"Invalid lookup type: {lookup_type!r}")
        return f"{function}({sql})", params

    def datetime_extract_sql(self, lookup_type, sql, params, tzname):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        return self.date_extract_sql(lookup_type, sql, params)

    def _trunc_format(self, lookup_type):
        try:
            return TRUNC_FORMATS[lookup_type.lower()]
        except KeyError:
            raise ValueError(f"Invalid truncation kind: {lookup_type!r}")

    def date_trunc_sql(self, lookup_type, sql, params, tzname=None):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        trunc_format = self._trunc_format(lookup_type)
        return f"DATE(TRUNC_TIMESTAMP(TIMESTAMP({sql}, TIME('00:00:00')), '{trunc_format}'))", params

    def datetime_trunc_sql(self, lookup_type, sql, params, tzname):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        return f"TRUNC_TIMESTAMP({sql}, '{self._trunc_format(lookup_type)}')", params

    def time_trunc_sql(self, lookup_type, sql, params, tzname=None):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        trunc_format = self._trunc_format(lookup_type)
        return f"TIME(TRUNC_TIMESTAMP(TIMESTAMP(CURRENT DATE, {sql}), '{trunc_format}'))", params

    def datetime_cast_date_sql(self, sql, params, tzname):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        return f"DATE({sql})", params

    def datetime_cast_time_sql(self, sql, params, tzname):
        sql, params = self._convert_sql_to_tz(sql, params, tzname)
        return f"TIME({sql})", params

    # Value adaptation

    def adapt_timefield_value(self, value):
        if value is None:
            return None

        # Expression values are adapted by the database.
        if hasattr(value, 'resolve_expression'):
            return value

        if timezone.is_aware(value):
            raise ValueError("Db2 for iSeries backend does not support timezone-aware times.")

        return value

    def adapt_datetimefield_value(self, value):
        if value is None:
            return None

        # Expression values are adapted by the database.
        if hasattr(value, 'resolve_expression'):
            return value

        if timezone.is_aware(value):
            if settings.USE_TZ:
                value = timezone.make_naive(value, self.connection.timezone)
            else:
                raise ValueError("Db2 for iSeries backend does not support timezone-aware times when USE_TZ is False")
        return value

    # Inserts. The insert compiler wraps the INSERT in SELECT ... FROM FINAL TABLE (...) to return
    # the generated columns, so no RETURNING clause is appended here.

    def returning_columns(self, fields):
        # Django 6.0+
        return '', ()

    def return_insert_columns(self, fields):
        # Django 5.2
        return '', ()

    def fetch_returned_rows(self, cursor, returning_params):
        # Django 6.0+
        return cursor.fetchall()

    def fetch_returned_insert_columns(self, cursor, returning_params):
        # Django 5.2
        return cursor.fetchone()

    def fetch_returned_insert_rows(self, cursor):
        # Django 5.2
        return cursor.fetchall()

    def last_insert_id(self, cursor, table_name, pk_name):
        # IDENTITY_VAL_LOCAL() returns DECIMAL(31, 0) and ignores values generated by triggers.
        cursor.execute('SELECT IDENTITY_VAL_LOCAL() AS IDENTITY FROM SYSIBM.SYSDUMMY1')
        value = cursor.fetchone()[0]
        return None if value is None else int(value)

    def has_after_insert_triggers(self, table):
        """
        Whether the table has AFTER INSERT triggers (SQL or external). Cached per connection
        object: a trigger added while the application runs is seen after a restart.
        """
        table = table.upper()
        if table not in self._after_insert_trigger_cache:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM QSYS2.SYSTRIGGERS WHERE EVENT_OBJECT_SCHEMA = CURRENT_SCHEMA "
                    "AND EVENT_OBJECT_TABLE = %s AND ACTION_TIMING = 'AFTER' "
                    "AND (EVENT_MANIPULATION = 'INSERT' OR EVENTINSERT = 'Y') FETCH FIRST 1 ROWS ONLY",
                    [table],
                )
                self._after_insert_trigger_cache[table] = cursor.fetchone() is not None
        return self._after_insert_trigger_cache[table]

    def bulk_batch_size(self, fields, objs):
        # Django 6.1 limits batches by max_query_params by default; 5.2 and 6.0 don't.
        if not fields:
            return len(objs)
        batch_size = max(self.connection.features.max_query_params // len(fields), 1)
        if len(fields) > 1 and fields[0] is fields[1] and getattr(fields[0], 'primary_key', False):
            # bulk_update() passes [pk, pk, *fields]. Each object becomes a CASE branch, and Db2 for i
            # rejects a CASE with a few thousand branches (SQL0101); small batches are also faster.
            batch_size = min(batch_size, BULK_UPDATE_BATCH_SIZE)
        return batch_size

    def bulk_insert_sql(self, fields, placeholder_rows):
        placeholder_rows_sql = (", ".join(row) for row in placeholder_rows)
        values_sql = ", ".join(f'({sql})' for sql in placeholder_rows_sql)
        return f"VALUES {values_sql}"

    # Querying

    def limit_offset_sql(self, low_mark, high_mark):
        # Db2 for i rejects a bare "OFFSET n"; it needs the ROWS keyword.
        limit, offset = self._get_limit_offset_params(low_mark, high_mark)
        return ' '.join(sql for sql in (
            ('OFFSET %d ROWS' % offset) if offset else None,
            ('FETCH FIRST %d ROWS ONLY' % limit) if limit else None,
        ) if sql)

    # In case of WHERE clause, if the search is required to be case insensitive then converting
    # left hand side field to upper.
    def lookup_cast(self, lookup_type, internal_type=None):
        if lookup_type in ('iexact', 'icontains', 'istartswith', 'iendswith'):
            return "UPPER(%s)"
        return "%s"

    # As DB2 v91 specifications,
    # Maximum length of a table name and Maximum length of a column name is 128
    # http://publib.boulder.ibm.com/infocenter/db2e/v9r1/index.jsp?topic=/com.ibm.db2e.doc/db2elimits.html
    def max_name_length(self):
        return 128

    def no_limit_value(self):
        return None

    # Function to quote the name of schema, table and column.
    def quote_name(self, name=None):
        name = name.upper()
        if (name.startswith("\"") & name.endswith("\"")):
            return name

        if (name.startswith("\"")):
            return "%s\"" % name

        if (name.endswith("\"")):
            return "\"%s" % name

        return "\"%s\"" % name

    def regex_lookup(self, lookup_type):
        if lookup_type == 'regex':
            return 'REGEXP_LIKE(%s, %s)'
        return "REGEXP_LIKE(%s, %s, 'i')"

    def for_update_sql(self, nowait=False, skip_locked=False, of=(), no_key=False):
        """
        Return the FOR UPDATE SQL clause to lock rows for an update operation.
        """
        # DB2 doesn't support nowait select for update
        if nowait:
            raise utils.NotSupportedError("NOWAIT clause not supported by Db2 for iSeries")
        return 'FOR UPDATE%s%s' % (
            ' OF %s' % ', '.join(of) if of else '',
            ' SKIP LOCKED DATA' if skip_locked else '',
        )

    # Savepoints

    def savepoint_create_sql(self, sid):
        return "SAVEPOINT %s ON ROLLBACK RETAIN CURSORS" % sid

    def savepoint_commit_sql(self, sid):
        return "RELEASE TO SAVEPOINT %s" % sid

    def savepoint_rollback_sql(self, sid):
        return "ROLLBACK TO SAVEPOINT %s" % sid

    # Flush and sequences

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        """
        Delete all rows from the given tables. Foreign keys that reference them are dropped first
        and re-created afterwards, because Db2 for i cannot defer constraint checks.
        """
        if not tables:
            return []

        flushed_tables = {table.upper() for table in tables}
        constraints = set()
        for table in tables:
            for constraint in self._foreign_keys_referencing(table):
                if allow_cascade:
                    flushed_tables.add(constraint.referencing_table)
                constraints.add(constraint)

        sqls = [self._drop_constraint_sql(constraint) for constraint in sorted(constraints)]
        # Changes must be committed before tables touched in this transaction can be altered (SQL0910).
        sqls.append('COMMIT')
        sqls.extend(
            '%s %s %s' % (style.SQL_KEYWORD('DELETE'), style.SQL_KEYWORD('FROM'), style.SQL_TABLE(self.quote_name(table)))
            for table in sorted(flushed_tables)
        )
        sqls.append('COMMIT')
        if reset_sequences:
            sequences = [
                sequence for sequence in self.connection.introspection.sequence_list()
                if sequence['table'].upper() in flushed_tables
            ]
            sqls.extend(self.sequence_reset_by_name_sql(style, sequences))
        sqls.extend(self._add_constraint_sql(constraint) for constraint in sorted(constraints))
        return sqls

    def execute_sql_flush(self, sql_list):
        """
        sql_flush() commits between dropping foreign keys and re-adding them, so a failure part way
        would leave them dropped. On failure, re-add every foreign key that is missing, then re-raise.
        """
        try:
            super().execute_sql_flush(sql_list)
        except Exception:
            for sql in sql_list:
                if ' ADD CONSTRAINT ' in sql and ' FOREIGN KEY ' in sql:
                    try:
                        with self.connection.cursor() as cursor:
                            cursor.execute(sql)
                    except utils.Error:
                        pass  # still present (it was never dropped) or can't be re-added
            raise

    def sequence_reset_sql(self, style, model_list):
        """
        Note: Db2 for iSeries cannot reset a table's primary key sequence if you have an open transaction
        that has already inserted/updated that table.
        """
        sqls = []
        with self.connection.cursor() as cursor:
            for model in model_list:
                table = model._meta.db_table
                for field in model._meta.local_fields:
                    if isinstance(field, AutoFieldMixin):
                        cursor.execute("SELECT MAX(%s) FROM %s" % (self.quote_name(field.column), self.quote_name(table)))
                        max_id = cursor.fetchone()[0] or 0
                        sqls.append(self._restart_identity_sql(style, table, field.column, max_id + 1))
                        break
        return sqls

    def sequence_reset_by_name_sql(self, style, sequences):
        return [
            self._restart_identity_sql(style, sequence['table'], sequence['column'], 1)
            for sequence in sequences
            if sequence['column'] is not None
        ]

    def _restart_identity_sql(self, style, table, column, restart_with):
        return '%s %s %s %s %s' % (
            style.SQL_KEYWORD('ALTER TABLE'),
            style.SQL_TABLE(self.quote_name(table)),
            style.SQL_KEYWORD('ALTER COLUMN'),
            style.SQL_FIELD(self.quote_name(column)),
            style.SQL_KEYWORD('RESTART WITH %d' % restart_with),
        )

    def _foreign_keys_referencing(self, table):
        """Return the enabled foreign keys in the current schema that reference the given table."""
        foreign_keys_sql = """
            SELECT FK.TABLE_NAME, FK.CONSTRAINT_NAME, FKC.COLUMN_NAME, PK.TABLE_NAME, PKC.COLUMN_NAME,
                   REF.DELETE_RULE, REF.UPDATE_RULE
              FROM QSYS2.SYSREFCST REF
              JOIN QSYS2.SYSCST FK
                ON FK.CONSTRAINT_SCHEMA = REF.CONSTRAINT_SCHEMA AND FK.CONSTRAINT_NAME = REF.CONSTRAINT_NAME
              JOIN QSYS2.SYSCST PK
                ON PK.CONSTRAINT_SCHEMA = REF.UNIQUE_CONSTRAINT_SCHEMA
               AND PK.CONSTRAINT_NAME = REF.UNIQUE_CONSTRAINT_NAME
              JOIN QSYS2.SYSKEYCST FKC
                ON FKC.CONSTRAINT_SCHEMA = FK.CONSTRAINT_SCHEMA AND FKC.CONSTRAINT_NAME = FK.CONSTRAINT_NAME
              JOIN QSYS2.SYSKEYCST PKC
                ON PKC.CONSTRAINT_SCHEMA = PK.CONSTRAINT_SCHEMA AND PKC.CONSTRAINT_NAME = PK.CONSTRAINT_NAME
               AND PKC.ORDINAL_POSITION = FKC.ORDINAL_POSITION
             WHERE PK.TABLE_SCHEMA = CURRENT_SCHEMA
               AND PK.TABLE_NAME = %s
               AND FK.ENABLED = 'YES'
             ORDER BY FK.TABLE_NAME, FK.CONSTRAINT_NAME, FKC.ORDINAL_POSITION
        """
        with self.connection.cursor() as cursor:
            cursor.execute(foreign_keys_sql, [table.upper()])
            rows = cursor.fetchall()

        # One row per column: group multi-column foreign keys into a single constraint.
        grouped = {}
        for referencing_table, name, referencing_column, referenced_table, referenced_column, delete_rule, update_rule in rows:
            key = (referencing_table, name)
            if key not in grouped:
                grouped[key] = (referenced_table, [], [], delete_rule, update_rule)
            grouped[key][1].append(referencing_column)
            grouped[key][2].append(referenced_column)
        return [
            ForeignKeyConstraint(referencing_table, name, tuple(referencing_columns), referenced_table,
                                 tuple(referenced_columns), delete_rule, update_rule)
            for (referencing_table, name), (referenced_table, referencing_columns, referenced_columns, delete_rule,
                                            update_rule) in grouped.items()
        ]

    def _drop_constraint_sql(self, constraint):
        return 'ALTER TABLE %s DROP CONSTRAINT %s' % (
            self.quote_name(constraint.referencing_table), self.quote_name(constraint.name))

    def _add_constraint_sql(self, constraint):
        return 'ALTER TABLE %s ADD CONSTRAINT %s FOREIGN KEY (%s) REFERENCES %s (%s) ON DELETE %s ON UPDATE %s' % (
            self.quote_name(constraint.referencing_table),
            self.quote_name(constraint.name),
            ', '.join(self.quote_name(column) for column in constraint.referencing_columns),
            self.quote_name(constraint.referenced_table),
            ', '.join(self.quote_name(column) for column in constraint.referenced_columns),
            constraint.delete_rule,
            constraint.update_rule,
        )
