"""
Db2 for i specific SQL for Django expressions, registered as as_db2iseries() methods (Django calls
as_<connection.vendor>() when it exists).
"""
from django.db.models import Value, When
from django.db.models.functions import Cast, Random


def when_as_db2iseries(self, compiler, connection, **extra_context):
    # A CASE needs at least one typed result; a bare parameter marker is untyped (SQL0580).
    when = self
    if isinstance(self.result, Value) and self.result.value is not None:
        when = self.copy()
        when.result = Cast(self.result, output_field=self.result.output_field)
    return When.as_sql(when, compiler, connection, **extra_context)


def random_as_db2iseries(self, compiler, connection, **extra_context):
    # RAND() exists on every supported release; RANDOM() only on newer ones.
    return self.as_sql(compiler, connection, function='RAND', **extra_context)


def register():
    When.as_db2iseries = when_as_db2iseries
    Random.as_db2iseries = random_as_db2iseries
