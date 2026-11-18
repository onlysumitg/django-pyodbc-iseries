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
from collections import namedtuple

from django.db.backends.base.introspection import BaseDatabaseIntrospection, FieldInfo as BaseFieldInfo
from django.db.models.fields import AutoFieldMixin

FieldInfo = namedtuple('FieldInfo', BaseFieldInfo._fields + ('label', 'hint', 'ccsid'))

# CCSID of binary character data (CHAR/VARCHAR FOR BIT DATA).
BINARY_CCSID = 65535

TableInfo = namedtuple('TableInfo', ['name', 'type', 'long_name'])

# QSYS2.SYSTABLES.TABLE_TYPE -> Django table type ('t' table, 'v' view).
# Physical files (P) are tables; logical files (L), aliases (A) and materialized query tables (M)
# are reported as views so Django never tries to manage them as tables.
TABLE_TYPES = {'T': 't', 'P': 't', 'V': 'v', 'L': 'v', 'A': 'v', 'M': 'v'}

# Tables are looked up by their SQL name. Tables with names longer than 10 characters also get a
# generated system name (for example DJANGO_CONTENT_TYPE -> DJANG00001); that name is accepted too,
# so models whose db_table holds a system name keep working.
SYSTEM_NAME_MAX_LENGTH = 10


class DatabaseIntrospection(BaseDatabaseIntrospection):
    """
    This is the class where database metadata information can be generated.
    """

    # QSYS2.SYSCOLUMNS.DATA_TYPE -> Django field. REAL, DOUBLE and FLOAT all report as FLOAT;
    # NCHAR/NVARCHAR/NCLOB report as GRAPHIC/VARG/DBCLOB.
    data_types_reverse = {
        'CHAR': 'CharField',
        'VARCHAR': 'CharField',
        'GRAPHIC': 'CharField',
        'VARG': 'CharField',
        'DATALINK': 'CharField',
        'CLOB': 'TextField',
        'DBCLOB': 'TextField',
        'XML': 'TextField',
        'BLOB': 'BinaryField',
        'BINARY': 'BinaryField',
        'VARBIN': 'BinaryField',
        'ROWID': 'BinaryField',
        'SMALLINT': 'SmallIntegerField',
        'INTEGER': 'IntegerField',
        'BIGINT': 'BigIntegerField',
        'DECIMAL': 'DecimalField',
        'NUMERIC': 'DecimalField',
        'DECFLOAT': 'DecimalField',
        'FLOAT': 'FloatField',
        'DOUBLE': 'FloatField',
        'REAL': 'FloatField',
        'DATE': 'DateField',
        'TIME': 'TimeField',
        'TIMESTMP': 'DateTimeField',
        'TIMESTAMP': 'DateTimeField',
        'BOOLEAN': 'BooleanField',
    }

    identity_field_types = {
        'SMALLINT': 'SmallAutoField',
        'INTEGER': 'AutoField',
        'BIGINT': 'BigAutoField',
    }

    def get_field_type(self, data_type, description):
        # CHAR/VARCHAR FOR BIT DATA (CCSID 65535) hold bytes, not text.
        if data_type in ('CHAR', 'VARCHAR') and description.ccsid == BINARY_CCSID:
            return 'BinaryField'
        # Identity columns are reported with a default starting with '**' (see get_table_description).
        if description.default and description.default.startswith('**') and data_type in self.identity_field_types:
            return self.identity_field_types[data_type]
        return super().get_field_type(data_type, description)

    def identifier_converter(self, name):
        return name.strip().lower()

    def _resolve_table(self, cursor, table_name):
        """
        Return (schema, table) holding the given table's data: an alias resolves to its base table.
        Raises ValueError if the table does not exist in the current schema.
        """
        schema = cursor.get_current_schema()
        table_name = table_name.upper()
        sql = ("SELECT TABLE_NAME, TABLE_TYPE, BASE_TABLE_SCHEMA, BASE_TABLE_NAME FROM QSYS2.SYSTABLES "
               "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s")
        params = [schema, table_name]
        if len(table_name) <= SYSTEM_NAME_MAX_LENGTH:
            # A longer value can't be a system name, and the driver rejects it for the CHAR(10) column.
            sql = sql.replace("TABLE_NAME = %s", "(TABLE_NAME = %s OR SYSTEM_TABLE_NAME = %s)")
            params.append(table_name)
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Table {table_name} does not exist in schema {schema}")
        name, table_type, base_schema, base_name = row
        if table_type == 'A' and base_name:
            return base_schema.strip(), base_name.strip()
        return schema, name.strip()

    # Getting the list of all tables, which are present under current schema.
    def get_table_list(self, cursor):
        schema = cursor.get_current_schema()
        cursor.execute("SELECT TABLE_NAME, TABLE_TYPE FROM QSYS2.SYSTABLES WHERE TABLE_SCHEMA = %s", [schema])
        return [
            TableInfo(self.identifier_converter(name), TABLE_TYPES.get(table_type, 'v'), self.identifier_converter(name))
            for name, table_type in cursor.fetchall()
        ]

    # Getting the description of the table.
    def get_table_description(self, cursor, table_name):
        schema, table_name = self._resolve_table(cursor, table_name)
        cursor.execute(
            """SELECT COLUMN_NAME,
                      TRIM(DATA_TYPE),
                      LENGTH,
                      STORAGE,
                      NUMERIC_PRECISION,
                      NUMERIC_SCALE,
                      CASE IS_NULLABLE WHEN 'Y' THEN 1 ELSE 0 END,
                      CASE WHEN IDENTITY_GENERATION IS NULL THEN COLUMN_DEFAULT
                           ELSE CONCAT('**', IFNULL(COLUMN_DEFAULT, '')) END,
                      IFNULL(COLUMN_HEADING, ''),
                      IFNULL(COLUMN_TEXT, ''),
                      CCSID
                 FROM QSYS2.SYSCOLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION""",
            [schema, table_name],
        )
        return [
            FieldInfo(
                self.identifier_converter(name),
                data_type,
                length,
                storage,
                precision,
                scale,
                is_nullable == 1,
                default.replace("'", "") if default else None,
                None,
                ' '.join(heading.split()),
                ' '.join(text.split()),
                ccsid,
            )
            for name, data_type, length, storage, precision, scale, is_nullable, default, heading, text, ccsid
            in cursor.fetchall()
        ]

    def get_relations(self, cursor, table_name):
        """Return {field_column: (referenced_column, referenced_table)} for foreign keys on the table."""
        schema, table_name = self._resolve_table(cursor, table_name)
        cursor.execute(
            """SELECT FKC.COLUMN_NAME, PKC.COLUMN_NAME, PK.TABLE_NAME
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
                WHERE FK.TABLE_SCHEMA = %s AND FK.TABLE_NAME = %s""",
            [schema, table_name],
        )
        return {
            self.identifier_converter(column): (self.identifier_converter(referenced_column),
                                                self.identifier_converter(referenced_table))
            for column, referenced_column, referenced_table in cursor.fetchall()
        }

    def get_constraints(self, cursor, table_name):
        schema, table_name = self._resolve_table(cursor, table_name)
        constraints = {}

        # Primary keys, unique constraints and foreign keys, with columns in key order.
        cursor.execute(
            """SELECT CST.CONSTRAINT_NAME, CST.CONSTRAINT_TYPE, K.COLUMN_NAME, PK.TABLE_NAME, PKC.COLUMN_NAME
                 FROM QSYS2.SYSCST CST
                 JOIN QSYS2.SYSKEYCST K
                   ON K.CONSTRAINT_SCHEMA = CST.CONSTRAINT_SCHEMA AND K.CONSTRAINT_NAME = CST.CONSTRAINT_NAME
                 LEFT JOIN QSYS2.SYSREFCST REF
                   ON REF.CONSTRAINT_SCHEMA = CST.CONSTRAINT_SCHEMA AND REF.CONSTRAINT_NAME = CST.CONSTRAINT_NAME
                 LEFT JOIN QSYS2.SYSCST PK
                   ON PK.CONSTRAINT_SCHEMA = REF.UNIQUE_CONSTRAINT_SCHEMA
                  AND PK.CONSTRAINT_NAME = REF.UNIQUE_CONSTRAINT_NAME
                 LEFT JOIN QSYS2.SYSKEYCST PKC
                   ON PKC.CONSTRAINT_SCHEMA = PK.CONSTRAINT_SCHEMA AND PKC.CONSTRAINT_NAME = PK.CONSTRAINT_NAME
                  AND PKC.ORDINAL_POSITION = K.ORDINAL_POSITION
                WHERE CST.TABLE_SCHEMA = %s AND CST.TABLE_NAME = %s
                ORDER BY CST.CONSTRAINT_NAME, K.ORDINAL_POSITION""",
            [schema, table_name],
        )
        for name, constraint_type, column, referenced_table, referenced_column in cursor.fetchall():
            constraint = constraints.setdefault(self.identifier_converter(name), {
                'columns': [],
                'primary_key': constraint_type == 'PRIMARY KEY',
                'unique': constraint_type in ('PRIMARY KEY', 'UNIQUE'),
                'foreign_key': (
                    (self.identifier_converter(referenced_table), self.identifier_converter(referenced_column))
                    if constraint_type == 'FOREIGN KEY' else None
                ),
                'check': False,
                'index': False,
            })
            constraint['columns'].append(self.identifier_converter(column))

        # Check constraints.
        cursor.execute(
            """SELECT CST.CONSTRAINT_NAME, COL.COLUMN_NAME
                 FROM QSYS2.SYSCST CST
                 JOIN QSYS2.SYSCSTCOL COL
                   ON COL.CONSTRAINT_SCHEMA = CST.CONSTRAINT_SCHEMA AND COL.CONSTRAINT_NAME = CST.CONSTRAINT_NAME
                WHERE CST.TABLE_SCHEMA = %s AND CST.TABLE_NAME = %s AND CST.CONSTRAINT_TYPE = 'CHECK'
                ORDER BY CST.CONSTRAINT_NAME, COL.COLUMN_NAME""",
            [schema, table_name],
        )
        for name, column in cursor.fetchall():
            constraints.setdefault(self.identifier_converter(name), {
                'columns': [],
                'primary_key': False,
                'unique': False,
                'foreign_key': None,
                'check': True,
                'index': False,
            })['columns'].append(self.identifier_converter(column))

        # Indexes.
        cursor.execute(
            """SELECT IDX.INDEX_NAME, K.COLUMN_NAME, IDX.IS_UNIQUE, K.ORDERING
                 FROM QSYS2.SYSINDEXES IDX
                 JOIN QSYS2.SYSKEYS K
                   ON K.INDEX_SCHEMA = IDX.INDEX_SCHEMA AND K.INDEX_NAME = IDX.INDEX_NAME
                WHERE IDX.TABLE_SCHEMA = %s AND IDX.TABLE_NAME = %s
                ORDER BY IDX.INDEX_NAME, K.ORDINAL_POSITION""",
            [schema, table_name],
        )
        for name, column, is_unique, ordering in cursor.fetchall():
            index = constraints.setdefault(self.identifier_converter(name), {
                'columns': [],
                'orders': [],
                'primary_key': False,
                # 'D' allows duplicate keys; 'U' and 'V' (unique where not null) are unique.
                'unique': is_unique in ('U', 'V'),
                'foreign_key': None,
                'check': False,
                'index': True,
                'type': 'idx',
            })
            index['columns'].append(self.identifier_converter(column))
            index['orders'].append('DESC' if ordering == 'D' else 'ASC')
        return constraints

    def get_sequences(self, cursor, table_name, table_fields=()):
        for field in table_fields:
            if isinstance(field, AutoFieldMixin):
                return [{'table': table_name, 'column': field.column}]
        # No model field given: fall back to the table's identity column, if any.
        schema, resolved_table = self._resolve_table(cursor, table_name)
        cursor.execute(
            "SELECT COLUMN_NAME FROM QSYS2.SYSCOLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "AND IS_IDENTITY = 'YES'",
            [schema, resolved_table],
        )
        return [{'table': table_name, 'column': self.identifier_converter(column)} for (column,) in cursor.fetchall()]
