# +--------------------------------------------------------------------------+
# |  Licensed Materials - Property of IBM                                    |
# |                                                                          |
# | (C) Copyright IBM Corporation 2009-2018.                                 |
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

import sys

from django import VERSION as djangoVersion
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db.backends.base.creation import BaseDatabaseCreation
from django.db.backends.utils import truncate_name

from . import Database

TEST_DBNAME_PREFIX = 'test_'


class DatabaseCreation(BaseDatabaseCreation):
    psudo_column_prefix = 'psudo_'

    data_types = {
        'AutoField': 'INTEGER GENERATED BY DEFAULT AS IDENTITY '
                     '(START WITH 1, INCREMENT BY 1, CACHE 10 ORDER)',  # DB2 Specific
        'BigAutoField': 'BIGINT GENERATED BY DEFAULT AS IDENTITY '
                        '(START WITH 1, INCREMENT BY 1, CACHE 10 ORDER)',  # DB2 Specific
        'CharField': 'VARCHAR(%(max_length)s)',
        'CommaSeparatedIntegerField': 'VARCHAR(%(max_length)s)',
        'DateField': 'DATE',
        'DateTimeField': 'TIMESTAMP',
        'DecimalField': 'DECIMAL(%(max_digits)s, %(decimal_places)s)',
        'FileField': 'VARCHAR(%(max_length)s)',
        'FilePathField': 'VARCHAR(%(max_length)s)',
        'FloatField': 'DOUBLE',
        'ImageField': 'VARCHAR(%(max_length)s)',
        'IntegerField': 'INTEGER',
        'BigIntegerField': 'BIGINT',
        'IPAddressField': 'VARCHAR(15)',
        'GenericIPAddressField': 'VARCHAR(39)',
        'ManyToManyField': 'VARCHAR(%(max_length)s)',
        'OneToOneField': 'VARCHAR(%(max_length)s)',
        'PhoneNumberField': 'VARCHAR(16)',
        'SlugField': 'VARCHAR(%(max_length)s)',
        'SmallIntegerField': 'SMALLINT',
        'TextField': 'CLOB',
        'TimeField': 'TIME',
        'USStateField': 'VARCHAR(2)',
        'URLField': 'VARCHAR2(%(max_length)s)',
        'XMLField': 'XML',
        'BinaryField': 'BLOB',
        'UUIDField': 'VARCHAR(255)',
        "DurationField": 'DOUBLE',
        'BooleanField': 'SMALLINT',
        'NullBooleanField': 'SMALLINT',
        'PositiveIntegerField': 'INTEGER',
        'PositiveSmallIntegerField': 'SMALLINT',
    }

    data_type_check_constraints = {
        'BooleanField': '%(attname)s IN (0,1)',
        'NullBooleanField': '(%(attname)s IN (0,1)) OR (%(attname)s IS NULL)',
        'PositiveIntegerField': '%(attname)s >= 0',
        'PositiveSmallIntegerField': '%(attname)s >= 0',
    }

    def sql_indexes_for_field(self, model, f, style):
        """Return the CREATE INDEX SQL statements for a single model field"""
        output = []
        qn = self.connection.ops.quote_name
        max_name_length = self.connection.ops.max_name_length()
        # ignore tablespace information
        tablespace_sql = ''
        i = 0
        if len(model._meta.unique_together_index) != 0:
            for unique_together_index in model._meta.unique_together_index:
                i = i + 1
                column_list = []
                for column in unique_together_index:
                    for local_field in model._meta.local_fields:
                        if column == local_field.name:
                            column_list.extend([local_field.column])

                self.__add_psudokey_column(style, self.connection.cursor(), model._meta.db_table,
                                           model._meta.pk.attname, column_list)
                column_list.extend(
                    [truncate_name("%s%s" % (self.psudo_column_prefix, "_".join(column_list)), max_name_length)])
                output.extend([style.SQL_KEYWORD('CREATE UNIQUE INDEX') + ' ' + \
                               style.SQL_TABLE(qn('db2_%s_%s' % (model._meta.db_table, i))) + ' ' + \
                               style.SQL_KEYWORD('ON') + ' ' + \
                               style.SQL_TABLE(qn(model._meta.db_table)) + ' ' + \
                               '( %s )' % ", ".join(column_list) + ' ' + \
                               '%s;' % tablespace_sql])
            model._meta.unique_together_index = []

        if f.unique_index:
            column_list = []
            column_list.extend([f.column])
            self.__add_psudokey_column(style, self.connection.cursor(), model._meta.db_table,
                                       model._meta.pk.attname, column_list)
            cisql = 'CREATE UNIQUE INDEX'
            output.extend([style.SQL_KEYWORD(cisql) + ' ' +
                           style.SQL_TABLE(qn('%s_%s' % (model._meta.db_table, f.column))) + ' ' +
                           style.SQL_KEYWORD('ON') + ' ' +
                           style.SQL_TABLE(qn(model._meta.db_table)) + ' ' +
                           "(%s, %s )" % (style.SQL_FIELD(qn(f.column)), style.SQL_FIELD(
                qn(truncate_name((self.psudo_column_prefix + f.column), max_name_length)))) +
                           "%s;" % tablespace_sql])
            return output

        if f.db_index and not f.unique:
            cisql = 'CREATE INDEX'
            output.extend([style.SQL_KEYWORD(cisql) + ' ' +
                           style.SQL_TABLE(qn('%s_%s' % (model._meta.db_table, f.column))) + ' ' +
                           style.SQL_KEYWORD('ON') + ' ' +
                           style.SQL_TABLE(qn(model._meta.db_table)) + ' ' +
                           "(%s)" % style.SQL_FIELD(qn(f.column)) +
                           "%s;" % tablespace_sql])

        return output

    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        if keepdb:
            return
        else:
            raise ImproperlyConfigured('pyodbc iseries driver does not support database creation')

    def _destroy_test_db(self, test_database_name, verbosity):
        raise ImproperlyConfigured('pyodbc iseries driver does not support database destruction (use --keepdb)')

    # As DB2 does not allow to insert NULL value in UNIQUE col, hence modifing model.
    def sql_create_model(self, model, style, known_models=set()):
        if getattr(self.connection.connection, dbms_name) != 'DB2':
            model._meta.unique_together_index = []
            temp_changed_uvalues = []
            temp_unique_together = model._meta.unique_together
            for i in range(len(model._meta.local_fields)):
                model._meta.local_fields[i].unique_index = False
                if model._meta.local_fields[i]._unique and model._meta.local_fields[i].null:
                    model._meta.local_fields[i].unique_index = True
                    model._meta.local_fields[i]._unique = False
                    temp_changed_uvalues.append(i)

                if len(model._meta.unique_together) != 0:
                    for unique_together in model._meta.unique_together:
                        if model._meta.local_fields[i].name in unique_together:
                            if model._meta.local_fields[i].null:
                                unique_list = list(model._meta.unique_together)
                                unique_list.remove(unique_together)
                                model._meta.unique_together = tuple(unique_list)
                                model._meta.unique_together_index.append(unique_together)
            sql, references = super(DatabaseCreation, self).sql_create_model(model, style, known_models)

            for i in temp_changed_uvalues:
                model._meta.local_fields[i]._unique = True
            model._meta.unique_together = temp_unique_together
            return sql, references
        else:
            return super(DatabaseCreation, self).sql_create_model(model, style, known_models)

    # Private method to clean up database.
    def __clean_up(self, cursor):
        tables = self.connection.introspection.django_table_names(only_existing=True)
        for table in tables:
            sql = "DROP TABLE %s" % self.connection.ops.quote_name(table)
            cursor.execute(sql)
        cursor.close()

    # Private method to alter a table with adding psudokey column
    def __add_psudokey_column(self, style, cursor, table_name, pk_name, column_list):
        qn = self.connection.ops.quote_name
        max_name_length = self.connection.ops.max_name_length()

        sql = style.SQL_KEYWORD('ALTER TABLE ') + \
              style.SQL_TABLE(qn(table_name)) + \
              style.SQL_KEYWORD(' ADD COLUMN ') + \
              style.SQL_FIELD(
                  qn(truncate_name("%s%s" % (self.psudo_column_prefix, "_".join(column_list)), max_name_length))) + \
              style.SQL_KEYWORD(' GENERATED ALWAYS AS( CASE WHEN ') + \
              style.SQL_FIELD("%s %s" % (" IS NULL OR ".join(column_list), 'IS NULL THEN ')) + \
              style.SQL_FIELD(qn(pk_name)) + \
              style.SQL_KEYWORD(' END ) ;')
        cursor.execute('SET INTEGRITY FOR ' + style.SQL_TABLE(qn(table_name)) + ' OFF CASCADE DEFERRED;')
        cursor.execute(sql)
        cursor.execute('SET INTEGRITY FOR ' + style.SQL_TABLE(table_name) + ' IMMEDIATE CHECKED;')
        cursor.close()

    # private method to create dictionary of login credentials for test database
    def __create_test_kwargs(self):
        if (isinstance(self.connection.settings_dict['NAME'], str) and
                (self.connection.settings_dict['NAME'] != '')):
            database = self.connection.settings_dict['NAME']
        else:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured("the database Name doesn't exist")
        database_user = self.connection.settings_dict['USER']
        database_pass = self.connection.settings_dict['PASSWORD']
        database_host = self.connection.settings_dict['HOST']
        database_port = self.connection.settings_dict['PORT']
        self.connection.settings_dict['SUPPORTS_TRANSACTIONS'] = True

        kwargs = {}
        kwargs['database'] = database
        if isinstance(database_user, str):
            kwargs['user'] = database_user

        if isinstance(database_pass, str):
            kwargs['password'] = database_pass

        if isinstance(database_host, str):
            kwargs['host'] = database_host

        if isinstance(database_port, str):
            kwargs['port'] = database_port

        if isinstance(database_host, str):
            kwargs['host'] = database_host

        return kwargs
