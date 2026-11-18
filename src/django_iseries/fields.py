"""
Model fields for Db2 for i specific column types.
"""
from django.db import models


class NVarCharField(models.CharField):
    """
    A CharField stored as NVARCHAR(max_length) (UTF-16, CCSID 1200) on Db2 for i, so max_length counts
    characters rather than UTF-8 bytes as CharField's VARCHAR(n) CCSID 1208 does. Characters outside
    the Basic Multilingual Plane (such as emoji) take two of the max_length positions.

    On other databases it behaves exactly like CharField.
    """

    description = "String (up to %(max_length)s characters, stored as NVARCHAR on Db2 for i)"

    def db_type(self, connection):
        if connection.vendor == 'db2iseries':
            return f'NVARCHAR({self.max_length})'
        return super().db_type(connection)
