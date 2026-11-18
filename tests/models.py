# copied from django.tests.models.py
from django.contrib.contenttypes.fields import (
    GenericForeignKey, GenericRelation,
)
from django.contrib.contenttypes.models import ContentType
from django.db import models

from django_iseries.fields import NVarCharField


class Square(models.Model):
    root = models.IntegerField()
    square = models.PositiveIntegerField()

    def __str__(self):
        return "%s ** 2 == %s" % (self.root, self.square)


class Person(models.Model):
    first_name = models.CharField(max_length=20)
    last_name = models.CharField(max_length=20)

    def __str__(self):
        return '%s %s' % (self.first_name, self.last_name)


class SchoolClass(models.Model):
    year = models.PositiveIntegerField()
    day = models.CharField(max_length=9, blank=True)
    last_updated = models.DateTimeField()


class VeryLongModelNameZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ(models.Model):
    primary_key_is_quite_long_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz = models.AutoField(primary_key=True)
    charfield_is_quite_long_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz = models.CharField(max_length=100)
    m2m_also_quite_long_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz = models.ManyToManyField(Person, blank=True)


class Tag(models.Model):
    name = models.CharField(max_length=30)
    content_type = models.ForeignKey(ContentType, models.CASCADE, related_name='backend_tags')
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')


class Post(models.Model):
    name = models.CharField(max_length=30)
    text = models.TextField()
    tags = GenericRelation('Tag')

    class Meta:
        db_table = 'CaseSensitive_Post'


class Reporter(models.Model):
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=30)

    def __str__(self):
        return "%s %s" % (self.first_name, self.last_name)


class ReporterProxy(Reporter):
    class Meta:
        proxy = True


class Article(models.Model):
    headline = models.CharField(max_length=100)
    pub_date = models.DateField()
    reporter = models.ForeignKey(Reporter, models.CASCADE)
    reporter_proxy = models.ForeignKey(
        ReporterProxy,
        models.SET_NULL,
        null=True,
        related_name='reporter_proxy',
    )

    def __str__(self):
        return self.headline


class Item(models.Model):
    name = models.CharField(max_length=30)
    date = models.DateField()
    time = models.TimeField()
    last_modified = models.DateTimeField()

    def __str__(self):
        return self.name


class Object(models.Model):
    related_objects = models.ManyToManyField("self", db_constraint=False, symmetrical=False)

    def __str__(self):
        return str(self.id)


class ObjectReference(models.Model):
    obj = models.ForeignKey(Object, models.CASCADE, db_constraint=False)

    def __str__(self):
        return str(self.obj_id)


class RawData(models.Model):
    raw_data = models.BinaryField()


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True)


class Book(models.Model):
    author = models.ForeignKey(Author, models.CASCADE, to_field='name')




class TypedValues(models.Model):
    flag = models.BooleanField(default=False)
    nullable_flag = models.BooleanField(null=True)
    ident = models.UUIDField(null=True)
    amount = models.DecimalField(max_digits=11, decimal_places=2, null=True)
    ratio = models.FloatField(null=True)
    big = models.BigIntegerField(null=True)
    small = models.SmallIntegerField(null=True)
    positive = models.PositiveIntegerField(null=True)
    url = models.URLField(null=True)
    text = models.TextField(null=True)
    data = models.BinaryField(null=True)
    duration = models.DurationField(null=True)
    stamp = models.DateTimeField(null=True)


class OrderLine(models.Model):
    """A table keyed by a composite primary key, as is common in IBM i applications."""
    pk = models.CompositePrimaryKey('order_no', 'line_no')
    order_no = models.IntegerField()
    line_no = models.IntegerField()
    product = models.CharField(max_length=30)
    quantity = models.IntegerField(default=1)

    class Meta:
        ordering = ['order_no', 'line_no']


class Db2Types(models.Model):
    """Maps an existing table that uses native Db2 for i column types (created by the tests)."""
    c_char = models.CharField(max_length=5, null=True)
    c_varchar = models.CharField(max_length=20, null=True)
    c_char_bit = models.BinaryField(null=True)
    c_varbinary = models.BinaryField(null=True)
    c_binary = models.BinaryField(null=True)
    c_blob = models.BinaryField(null=True)
    c_graphic = models.CharField(max_length=4, null=True)
    c_vargraphic = models.CharField(max_length=10, null=True)
    c_nvarchar = models.CharField(max_length=10, null=True)
    c_clob = models.TextField(null=True)
    c_dbclob = models.TextField(null=True)
    c_smallint = models.SmallIntegerField(null=True)
    c_integer = models.IntegerField(null=True)
    c_bigint = models.BigIntegerField(null=True)
    c_decimal = models.DecimalField(max_digits=9, decimal_places=2, null=True)
    c_numeric = models.DecimalField(max_digits=9, decimal_places=2, null=True)
    c_decfloat = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    c_real = models.FloatField(null=True)
    c_double = models.FloatField(null=True)
    c_date = models.DateField(null=True)
    c_time = models.TimeField(null=True)
    c_timestamp = models.DateTimeField(null=True)
    c_boolean = models.BooleanField(null=True)

    class Meta:
        managed = False
        db_table = 'DB2_TYPES'


class TriggeredItem(models.Model):
    """Its table gets an AFTER INSERT trigger that updates the inserted row (created by the tests)."""
    name = models.CharField(max_length=20)
    stamp = models.CharField(max_length=20, null=True)


class LegacyRecord(models.Model):
    """A keyless legacy table, keyed by relative record number (table created by the tests)."""
    id = models.AutoField(primary_key=True, db_column='RRN()')
    name = models.CharField(max_length=10)
    qty = models.DecimalField(max_digits=5, decimal_places=0)

    class Meta:
        managed = False
        db_table = 'legacy_record'


class LegacyLine(models.Model):
    """References LegacyRecord by record number (table created by the tests)."""
    record = models.ForeignKey(LegacyRecord, models.DO_NOTHING, db_column='RECORD_RRN', db_constraint=False)
    note = models.CharField(max_length=10)

    class Meta:
        managed = False
        db_table = 'legacy_line'


class UnicodeName(models.Model):
    name = NVarCharField(max_length=20)
    code = models.CharField(max_length=20, null=True)
