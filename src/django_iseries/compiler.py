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
# | Authors: Ambrish Bhargava, Tarun Pasrija, Rahul Priyadarshi              |
# +--------------------------------------------------------------------------+

import re

from django.db.models.fields import AutoFieldMixin
from django.db.models.sql import compiler

# A model can use the relative record number as its primary key through a pseudo column named
# "RRN()", for example AutoField(primary_key=True, db_column='RRN()'). Django renders it as
# "ALIAS"."RRN()", which is rewritten to the RRN("ALIAS") function, whatever the table alias.
RRN_COLUMN = 'RRN()'
RRN_COLUMN_REGEX = re.compile(r'("(?:[^"]|"")+")\."RRN\(\)"')


def _replace_rrn_columns(sql):
    return RRN_COLUMN_REGEX.sub(r'RRN(\1)', sql)


def _is_rrn(field):
    return field.column is not None and field.column.upper() == RRN_COLUMN


class SQLCompiler(compiler.SQLCompiler):
    def as_sql(self, *args, **kwargs):
        sql, params = super().as_sql(*args, **kwargs)
        return _replace_rrn_columns(sql), params


class SQLInsertCompiler(compiler.SQLInsertCompiler, SQLCompiler):
    def execute_sql(self, returning_fields=None):
        if returning_fields and any(_is_rrn(field) for field in returning_fields):
            # The record number of an inserted row can't be read back: RRN() over FINAL TABLE numbers
            # the result rows (1, 2, ...), not the records. Insert without returning anything, so new
            # objects keep pk=None instead of getting another row's record number.
            super().execute_sql(None)
            return [tuple(None for _ in returning_fields) for _ in self.query.objs]
        table = self.query.get_meta().db_table
        if returning_fields and self.connection.ops.has_after_insert_triggers(table):
            return self._execute_one_by_one(returning_fields)
        return super().execute_sql(returning_fields)

    def _execute_one_by_one(self, returning_fields):
        """
        Insert rows with plain INSERTs and read the generated values back afterwards.

        Used for tables with AFTER INSERT triggers: when such a trigger changes the inserted rows,
        SELECT ... FROM FINAL TABLE (INSERT ...) waits for the record lock and then fails (SQL0723).
        """
        opts = self.query.get_meta()
        qn = self.connection.ops.quote_name
        table = opts.db_table
        pk_fields = getattr(opts, 'pk_fields', [opts.pk])
        pk_is_generated = isinstance(opts.pk, AutoFieldMixin)
        returns_only_pk = [field.column for field in returning_fields] == [opts.pk.column]
        select_returning = 'SELECT %s FROM %s WHERE %s' % (
            ', '.join(qn(field.column) for field in returning_fields),
            qn(table),
            ' AND '.join(f'{qn(field.column)} = %s' for field in pk_fields),
        )

        objs = self.query.objs
        self.returning_fields = None
        rows = []
        try:
            with self.connection.cursor() as cursor:
                for obj in objs:
                    self.query.objs = [obj]
                    for sql, params in self.as_sql():
                        cursor.execute(sql, params)
                    if pk_is_generated:
                        pk_values = (self.connection.ops.last_insert_id(cursor, table, opts.pk.column),)
                    else:
                        pk_values = tuple(
                            field.get_db_prep_value(getattr(obj, field.attname), self.connection)
                            for field in pk_fields
                        )
                    if pk_is_generated and returns_only_pk:
                        rows.append(pk_values)
                    else:
                        cursor.execute(select_returning, pk_values)
                        rows.append(cursor.fetchone())
        finally:
            self.query.objs = objs
            self.returning_fields = returning_fields

        converters = self.get_converters([field.get_col(table) for field in returning_fields])
        if converters:
            rows = self.apply_converters(rows, converters)
        return list(rows)

    def as_sql(self):
        statements = super().as_sql()
        if not self.returning_fields:
            return statements

        # Db2 for i has no RETURNING clause; select the generated values from the inserted rows instead.
        # ORDER BY INPUT SEQUENCE returns them in insertion order, which bulk_create relies on.
        qn = self.connection.ops.quote_name
        opts = self.query.get_meta()
        table = qn(opts.db_table)
        returning_columns = ', '.join(qn(field.column) for field in self.returning_fields)
        return [
            (f'SELECT {returning_columns} FROM FINAL TABLE ({sql}) AS {table} ORDER BY INPUT SEQUENCE', params)
            for sql, params in statements
        ]


class SQLDeleteCompiler(compiler.SQLDeleteCompiler, SQLCompiler):
    def as_sql(self):
        sql, params = super().as_sql()
        return _replace_rrn_columns(sql), params


class SQLUpdateCompiler(compiler.SQLUpdateCompiler, SQLCompiler):
    def as_sql(self):
        sql, params = super().as_sql()
        return _replace_rrn_columns(sql), params


class SQLAggregateCompiler(compiler.SQLAggregateCompiler, SQLCompiler):
    def as_sql(self):
        sql, params = super().as_sql()
        return _replace_rrn_columns(sql), params
