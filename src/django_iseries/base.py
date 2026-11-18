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

"""
DB2 database backend for Django.
Requires: ibm_db_dbi (http://pypi.python.org/pypi/ibm_db) for python
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import utils
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.base.features import BaseDatabaseFeatures
from django.db.backends.base.validation import BaseDatabaseValidation

# Importing internal classes from iseries package.
from django_iseries.pybase import DatabaseWrapper as PyBaseDatabaseWrapper
from django_iseries.client import DatabaseClient
from django_iseries.creation import DatabaseCreation
from django_iseries.introspection import DatabaseIntrospection
from django_iseries.operations import DatabaseOperations
from django_iseries.schemaEditor import DB2SchemaEditor
from django_iseries import functions
from . import Database

dbms_name = 'dbname'

functions.register()


class DatabaseFeatures(BaseDatabaseFeatures):
    allows_group_by_selected_pks = False
    # Db2 for i doesn't accept GROUP BY <select list position>.
    allows_group_by_select_index = False
    # Untyped parameter markers aren't allowed as CASE results (SQL0580), so bulk_update casts them.
    requires_casted_case_in_updates = True

    interprets_empty_strings_as_nulls = False

    can_use_chunked_reads = True
    # Generated values are returned with SELECT ... FROM FINAL TABLE (INSERT ...); see compiler.py.
    can_return_columns_from_insert = True
    can_return_rows_from_bulk_insert = True
    has_bulk_insert = True
    # Db2 for i rejects statements with about 32,000 parameter markers (SQL0101, SQL0901); stay well below.
    max_query_params = 10000
    uses_savepoints = True
    can_release_savepoints = True

    related_fields_match_type = False
    has_select_for_update = True
    has_select_for_update_skip_locked = True
    has_select_for_update_of = True
    select_for_update_of_column = True
    supports_select_for_update_with_limit = False

    test_db_allows_multiple_connections = False

    supports_unspecified_pk = True

    supports_forward_references = False

    # transaction is supported by DB2
    supports_transactions = True

    allows_auto_pk_0 = True
    can_defer_constraint_checks = False
    supports_regex_backreferencing = True
    supports_timezones = False

    supports_paramstyle_pyformat = False
    supports_sequence_reset = True
    # DB2 doesn't take default values as parameter
    requires_literal_defaults = True
    # Db2 for i rejects DDL on a table changed earlier in the same transaction (SQL0910), and Django's
    # schema changes mix the two (for example, filling NULLs before SET NOT NULL). Migrations are
    # therefore not wrapped in a transaction; each statement commits on its own.
    can_rollback_ddl = False
    has_case_insensitive_like = False
    bare_select_suffix = ' FROM SYSIBM.SYSDUMMY1'
    implied_column_null = True
    ignores_table_name_case = True
    supports_over_clause = True

    # Oldest IBM i release the backend's SQL (TRUNC_TIMESTAMP, REGEXP_LIKE, OFFSET ... ROWS) is written for.
    minimum_database_version = (7, 3)

    # Db2 for i rejects "(a, b) IN ((?, ?), ...)" and "(a, b) > (?, ?)", so Django expands tuple
    # lookups (used by composite primary keys) into AND/OR conditions.
    supports_tuple_lookups = False

    # There is no JSON column type mapping.
    supports_json_field = False

    create_test_procedure_without_params_sql = None
    create_test_procedure_with_int_param_sql = None


class DatabaseValidation(BaseDatabaseValidation):
    # Need to do validation for DB2 and ibm_db version
    def validate_field(self, errors, opts, f):
        pass


class DatabaseWrapper(BaseDatabaseWrapper):
    """
    This is the base class for DB2 backend support for Django.
    """
    data_types = {}
    vendor = 'db2iseries'
    display_name = 'Db2 for i (pyodbc)'
    operators = {
        "exact": "= %s",
        "iexact": "LIKE UPPER(%s) ESCAPE '\\'",
        "contains": "LIKE %s ESCAPE '\\'",
        "icontains": "LIKE UPPER(%s) ESCAPE '\\'",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s ESCAPE '\\'",
        "endswith": "LIKE %s ESCAPE '\\'",
        "istartswith": "LIKE UPPER(%s) ESCAPE '\\'",
        "iendswith": "LIKE UPPER(%s) ESCAPE '\\'",
    }
    Database = Database

    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    validation_class = DatabaseValidation
    ops_class = DatabaseOperations

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, %, _)
    # should be escaped on the database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%', '\%'), '_', '\_')"
    pattern_ops = {
        'contains': "LIKE '%' || {} || '%' ESCAPE '\\'",
        'icontains': "LIKE '%' || UPPER({}) || '%' ESCAPE '\\'",
        'startswith': "LIKE {} || '%' ESCAPE '\\'",
        'istartswith': "LIKE UPPER({}) || '%' ESCAPE '\\'",
        'endswith': "LIKE '%' || {} ESCAPE '\\'",
        'iendswith': "LIKE '%' || UPPER({}) ESCAPE '\\'",
    }

    error_codes_remap_to_database_error = ['28000']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_types = self.creation.data_types
        self.data_type_check_constraints = self.creation.data_type_check_constraints
        self.databaseWrapper = PyBaseDatabaseWrapper()

    # To get dict of connection parameters 
    def get_connection_params(self):
        kwargs = {}

        settings_dict = self.settings_dict
        database_name = settings_dict['NAME']
        database_user = settings_dict['USER']
        database_pass = settings_dict['PASSWORD']
        database_host = settings_dict['HOST']
        database_port = settings_dict['PORT']
        database_options = settings_dict['OPTIONS']

        if database_name != '' and isinstance(database_name, str):
            kwargs['database'] = database_name
        else:
            raise ImproperlyConfigured("Please specify the valid database Name to connect to")

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

        if isinstance(database_options, dict):
            kwargs['options'] = database_options

        if (settings_dict.keys()).__contains__('PCONNECT'):
            kwargs['PCONNECT'] = settings_dict['PCONNECT']

        if 'CURRENTSCHEMA' in settings_dict:
            database_schema = settings_dict['CURRENTSCHEMA']
            if isinstance(database_schema, str):
                kwargs['currentschema'] = database_schema

        if 'SECURITY' in settings_dict:
            database_security = settings_dict['SECURITY']
            if isinstance(database_security, str):
                kwargs['security'] = database_security

        if 'SSLCLIENTKEYDB' in settings_dict:
            database_sslclientkeydb = settings_dict['SSLCLIENTKEYDB']
            if isinstance(database_sslclientkeydb, str):
                kwargs['sslclientkeydb'] = database_sslclientkeydb

        if 'SSLCLIENTKEYSTOREDBPASSWORD' in settings_dict:
            database_sslclientkeystoredbpassword: object = settings_dict['SSLCLIENTKEYSTOREDBPASSWORD']
            if isinstance(database_sslclientkeystoredbpassword, str):
                kwargs['sslclientkeystoredbpassword'] = database_sslclientkeystoredbpassword

        if 'SSLCLIENTKEYSTASH' in settings_dict:
            database_sslclientkeystash = settings_dict['SSLCLIENTKEYSTASH']
            if isinstance(database_sslclientkeystash, str):
                kwargs['sslclientkeystash'] = database_sslclientkeystash

        if 'SSLSERVERCERTIFICATE' in settings_dict:
            database_sslservercertificate = settings_dict['SSLSERVERCERTIFICATE']
            if isinstance(database_sslservercertificate, str):
                kwargs['sslservercertificate'] = database_sslservercertificate

        return kwargs

    # To get new connection from Database
    def get_new_connection(self, conn_params):
        connection = self.databaseWrapper.get_new_connection(conn_params)
        return connection

    # Over-riding _cursor method to return DB2 cursor.

    def create_cursor(self, name=None):
        return self.databaseWrapper._cursor(self.connection)

    def is_usable(self):
        if self.databaseWrapper.is_active(self.connection):
            return True
        else:
            return False

    def _set_autocommit(self, autocommit):
        self.connection.autocommit = autocommit

    def close(self):
        self.validate_thread_sharing()
        if self.connection is not None:
            self.databaseWrapper.close(self.connection)
            self.connection = None

    def get_server_version(self):
        if not self.connection:
            self.cursor()
        return self.databaseWrapper.get_server_version(self.connection)

    def get_database_version(self):
        # IBM i release as (version, release), e.g. (7, 5); checked against minimum_database_version.
        return self.get_server_version()[:2]

    def schema_editor(self, *args, **kwargs):
        return DB2SchemaEditor(self, *args, **kwargs)

    def connect(self):
        try:
            super().connect()
        except Database.InterfaceError as e:
            """Django expects errors such as invalid password to be DatabaseError"""
            if e.args[0] in self.error_codes_remap_to_database_error:
                raise utils.DatabaseError(*e.args)
            raise
