"""
ORM behavior against a live IBM i system (configured through .env; skipped when it isn't).

Each test runs inside a transaction that is rolled back afterwards, so the test library is left as
it was, except for the transactional test at the end, which exercises the flush path.
"""
import datetime
import decimal
import uuid

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import DataError, IntegrityError, connection, transaction
from django.db.models import Case, CharField, Count, F, Max, Q, Sum, Value, When
from django.db.models.functions import (
    Coalesce, Concat, ExtractHour, ExtractIsoYear, ExtractQuarter, ExtractWeek, ExtractWeekDay, Length, Lower,
    TruncDate, TruncDay, TruncMonth, TruncWeek, TruncYear, Upper,
)
from django.utils import timezone

from tests.models import (
    Article, Author, Book, Item, Object, OrderLine, Person, Reporter, Square, Tag, TypedValues,
)

pytestmark = pytest.mark.django_db


def make_item(name, date=datetime.date(2024, 5, 17), time=datetime.time(13, 45, 30),
              last_modified=datetime.datetime(2024, 5, 17, 13, 45, 30, tzinfo=datetime.timezone.utc)):
    return Item.objects.create(name=name, date=date, time=time, last_modified=last_modified)


# Basic CRUD

def test_create_assigns_generated_primary_key():
    first = Square.objects.create(root=2, square=4)
    second = Square.objects.create(root=3, square=9)
    assert first.pk is not None
    assert second.pk == first.pk + 1


def test_read_update_delete():
    person = Person.objects.create(first_name='Ada', last_name='Lovelace')
    fetched = Person.objects.get(pk=person.pk)
    assert (fetched.first_name, fetched.last_name) == ('Ada', 'Lovelace')

    fetched.last_name = 'King'
    fetched.save()
    assert Person.objects.get(pk=person.pk).last_name == 'King'

    assert Person.objects.filter(pk=person.pk).update(first_name='Augusta') == 1
    assert Person.objects.get(pk=person.pk).first_name == 'Augusta'

    fetched.delete()
    assert not Person.objects.filter(pk=person.pk).exists()


def test_bulk_create_returns_primary_keys_in_order():
    squares = Square.objects.bulk_create([Square(root=n, square=n * n) for n in (5, 1, 3)])
    assert all(square.pk is not None for square in squares)
    by_pk = {square.pk: square.root for square in Square.objects.filter(pk__in=[s.pk for s in squares])}
    assert [by_pk[square.pk] for square in squares] == [5, 1, 3]


def test_bulk_create_and_update_beyond_the_statement_parameter_limit():
    # 12,000 rows x 3 columns is 36,000 parameters, more than one Db2 for i statement accepts.
    people = Person.objects.bulk_create([Person(first_name=f'p{n}', last_name='x') for n in range(12000)])
    assert len({person.pk for person in people}) == 12000
    for person in people:
        person.last_name = 'y'
    assert Person.objects.bulk_update(people, ['last_name']) == 12000
    assert Person.objects.filter(last_name='y').count() == 12000
    assert len(Person.objects.in_bulk([person.pk for person in people])) == 12000


def test_bulk_update():
    people = Person.objects.bulk_create([Person(first_name=f'p{n}', last_name='x') for n in range(3)])
    for person in people:
        person.last_name = person.first_name.upper()
    assert Person.objects.bulk_update(people, ['last_name']) == 3
    assert sorted(Person.objects.values_list('last_name', flat=True)) == ['P0', 'P1', 'P2']


def test_get_or_create_and_update_or_create():
    person, created = Person.objects.get_or_create(first_name='Grace', defaults={'last_name': 'Hopper'})
    assert created
    same, created = Person.objects.get_or_create(first_name='Grace', defaults={'last_name': 'Other'})
    assert not created and same.pk == person.pk

    updated, created = Person.objects.update_or_create(first_name='Grace', defaults={'last_name': 'Murray'})
    assert not created and updated.last_name == 'Murray'


def test_queryset_delete():
    Person.objects.bulk_create([Person(first_name='del', last_name=str(n)) for n in range(4)])
    deleted, _ = Person.objects.filter(first_name='del').delete()
    assert deleted == 4


# Querying

def test_slicing_and_paging():
    Square.objects.bulk_create([Square(root=n, square=n * n) for n in range(1, 8)])
    roots = Square.objects.order_by('root')
    assert [s.root for s in roots[:3]] == [1, 2, 3]
    assert [s.root for s in roots[2:4]] == [3, 4]
    assert [s.root for s in roots[5:]] == [6, 7]
    assert roots.first().root == 1
    assert roots.last().root == 7


def test_aggregation_and_grouping():
    Square.objects.bulk_create([Square(root=n, square=n * n) for n in (1, 2, 2, 3)])
    assert Square.objects.aggregate(total=Sum('square'), highest=Max('root')) == {'total': 18, 'highest': 3}
    groups = dict(Square.objects.values_list('root').annotate(n=Count('id')).order_by('root'))
    assert groups == {1: 1, 2: 2, 3: 1}


def test_aggregate_over_sliced_queryset():
    Square.objects.bulk_create([Square(root=n, square=n) for n in range(10)])
    assert Square.objects.order_by('root')[:3].aggregate(total=Sum('square'))['total'] == 3


def test_distinct_values_and_exists():
    Person.objects.bulk_create([Person(first_name='a', last_name='x'), Person(first_name='a', last_name='y')])
    assert list(Person.objects.values_list('first_name', flat=True).distinct()) == ['a']
    assert Person.objects.filter(last_name='y').exists()
    assert not Person.objects.filter(last_name='z').exists()


def test_union_intersection_difference():
    Person.objects.bulk_create([Person(first_name=n, last_name='x') for n in ('a', 'b', 'c')])
    a_or_b = Person.objects.filter(first_name__in=['a', 'b']).values_list('first_name', flat=True)
    b_or_c = Person.objects.filter(first_name__in=['b', 'c']).values_list('first_name', flat=True)
    assert sorted(a_or_b.union(b_or_c)) == ['a', 'b', 'c']
    assert list(a_or_b.intersection(b_or_c)) == ['b']
    assert list(a_or_b.difference(b_or_c)) == ['a']


@pytest.mark.parametrize('lookup,value,expected', [
    ('exact', 'Mixed_Case%', ['Mixed_Case%']),
    ('iexact', 'mixed_case%', ['Mixed_Case%']),
    ('contains', '_Case%', ['Mixed_Case%']),
    ('icontains', 'MIXED', ['Mixed_Case%']),
    ('startswith', 'Mixed', ['Mixed_Case%']),
    ('istartswith', 'mixed', ['Mixed_Case%']),
    ('endswith', '%', ['Mixed_Case%']),
    ('iendswith', 'CASE%', ['Mixed_Case%']),
    ('contains', '%', ['Mixed_Case%']),
    ('contains', '_', ['Mixed_Case%']),
    ('regex', '^Mix', ['Mixed_Case%']),
    ('iregex', '^mix', ['Mixed_Case%']),
    ('in', ['plain', 'nope'], ['plain']),
])
def test_string_lookups(lookup, value, expected):
    Person.objects.create(first_name='Mixed_Case%', last_name='x')
    Person.objects.create(first_name='plain', last_name='x')
    found = Person.objects.filter(**{f'first_name__{lookup}': value}).values_list('first_name', flat=True)
    assert sorted(found) == expected


def test_null_range_and_q_lookups():
    TypedValues.objects.create(small=1)
    TypedValues.objects.create(small=5)
    TypedValues.objects.create(small=None)
    assert TypedValues.objects.filter(small__isnull=True).count() == 1
    assert TypedValues.objects.filter(small__range=(0, 2)).count() == 1
    assert TypedValues.objects.filter(Q(small=1) | Q(small=5)).count() == 2
    assert TypedValues.objects.exclude(small=1).filter(small__isnull=False).count() == 1


def test_expressions_and_functions():
    Person.objects.create(first_name='Ada', last_name='Lovelace')
    row = Person.objects.annotate(
        full=Concat('first_name', Value(' '), 'last_name', output_field=CharField()),
        lower=Lower('first_name'),
        upper=Upper('last_name'),
        length=Length('last_name'),
        label=Case(When(first_name='Ada', then=Value('match')), default=Value('other')),
        fallback=Coalesce('first_name', Value('none')),
        constant=Value("it's"),
    ).values('full', 'lower', 'upper', 'length', 'label', 'fallback', 'constant').get()
    assert row == {
        'full': 'Ada Lovelace', 'lower': 'ada', 'upper': 'LOVELACE', 'length': 8,
        'label': 'match', 'fallback': 'Ada', 'constant': "it's",
    }


def test_several_select_clause_parameters_keep_their_values():
    Person.objects.create(first_name='Ada', last_name='Lovelace')
    row = Person.objects.annotate(a=Value('A'), b=Value('B')).filter(last_name='Lovelace').values('a', 'b').get()
    assert row == {'a': 'A', 'b': 'B'}


def test_f_expression_arithmetic():
    square = Square.objects.create(root=3, square=9)
    Square.objects.filter(pk=square.pk).update(square=F('square') + F('root') * 2)
    square.refresh_from_db()
    assert square.square == 15


def test_random_ordering_and_iterator():
    Square.objects.bulk_create([Square(root=n, square=n) for n in range(5)])
    assert len(list(Square.objects.order_by('?'))) == 5
    assert sorted(s.root for s in Square.objects.iterator(chunk_size=2)) == [0, 1, 2, 3, 4]


# Dates and times

def test_date_lookups():
    make_item('a', date=datetime.date(2024, 12, 30))  # a Monday, in ISO week 1 of 2025
    item = Item.objects
    assert item.filter(date__year=2024).count() == 1
    assert item.filter(date__month=12, date__day=30).count() == 1
    assert item.filter(date__week_day=2).count() == 1  # Sunday is 1
    assert item.filter(date__iso_week_day=1).count() == 1
    assert item.filter(date__week=1, date__iso_year=2025).count() == 1
    assert item.filter(date__quarter=4).count() == 1
    assert item.filter(date__gte=datetime.date(2024, 12, 1)).count() == 1


def test_date_extract_and_truncate_functions():
    make_item('a', date=datetime.date(2024, 5, 17))
    row = Item.objects.annotate(
        month_start=TruncMonth('date'), year_start=TruncYear('date'), week_start=TruncWeek('date'),
        weekday=ExtractWeekDay('date'), quarter=ExtractQuarter('date'), week=ExtractWeek('date'),
        iso_year=ExtractIsoYear('date'),
    ).values('month_start', 'year_start', 'week_start', 'weekday', 'quarter', 'week', 'iso_year').get()
    assert row == {
        'month_start': datetime.date(2024, 5, 1), 'year_start': datetime.date(2024, 1, 1),
        'week_start': datetime.date(2024, 5, 13), 'weekday': 6, 'quarter': 2, 'week': 20, 'iso_year': 2024,
    }


def test_datetime_functions_and_lookups():
    stamp = datetime.datetime(2024, 5, 17, 13, 45, 30, 123456, tzinfo=datetime.timezone.utc)
    make_item('a', last_modified=stamp)
    row = Item.objects.annotate(
        day=TruncDay('last_modified'), date_only=TruncDate('last_modified'), hour=ExtractHour('last_modified'),
    ).values('day', 'date_only', 'hour').get()
    assert row == {
        'day': datetime.datetime(2024, 5, 17, tzinfo=datetime.timezone.utc),
        'date_only': datetime.date(2024, 5, 17), 'hour': 13,
    }
    assert Item.objects.filter(last_modified__date=datetime.date(2024, 5, 17)).count() == 1
    assert Item.objects.filter(last_modified__hour=13, last_modified__minute=45).count() == 1
    assert list(Item.objects.dates('date', 'month')) == [datetime.date(2024, 5, 1)]


def test_aware_datetimes_round_trip_in_utc():
    stamp = datetime.datetime(2024, 5, 17, 8, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))
    item = make_item('a', last_modified=stamp)
    item.refresh_from_db()
    assert item.last_modified == stamp
    assert item.last_modified.tzinfo is not None
    assert Item.objects.filter(last_modified=stamp).exists()


def test_time_values_round_trip_to_whole_seconds():
    # Db2 for i TIME columns have no fractional seconds.
    item = make_item('a', time=datetime.time(23, 59, 58, 500000))
    item.refresh_from_db()
    assert item.time == datetime.time(23, 59, 58)


# Field types

def test_field_values_round_trip():
    ident = uuid.uuid4()
    stamp = timezone.now().replace(microsecond=123456)
    values = TypedValues.objects.create(
        flag=True, nullable_flag=False, ident=ident, amount=decimal.Decimal('123456789.01'), ratio=0.25,
        big=2 ** 62, small=-7, positive=7, url='https://example.com/a?b=c', text='long text ' * 500,
        data=b'\x00\x01\xff', duration=datetime.timedelta(days=1, seconds=2, microseconds=3), stamp=stamp,
    )
    values.refresh_from_db()
    assert values.flag is True
    assert values.nullable_flag is False
    assert values.ident == ident
    assert values.amount == decimal.Decimal('123456789.01')
    assert values.ratio == 0.25
    assert values.big == 2 ** 62
    assert (values.small, values.positive) == (-7, 7)
    assert values.url == 'https://example.com/a?b=c'
    assert values.text == 'long text ' * 500
    assert bytes(values.data) == b'\x00\x01\xff'
    assert values.duration == datetime.timedelta(days=1, seconds=2, microseconds=3)
    assert values.stamp == stamp


def test_large_binary_value_round_trip():
    # BLOB values are fetched inline (see MAXFIELDLEN in pybase); locator fetches hang in pyodbc.
    data = bytes(range(256)) * 800  # 200 KB
    values = TypedValues.objects.create(data=data)
    values.refresh_from_db()
    assert bytes(values.data) == data


def test_boolean_filters():
    TypedValues.objects.create(flag=True)
    TypedValues.objects.create(flag=False)
    assert TypedValues.objects.filter(flag=True).count() == 1
    assert TypedValues.objects.filter(flag=False).count() == 1
    assert TypedValues.objects.get(flag=True).flag is True


def test_unicode_text_round_trip():
    text = 'héé ñ € 日本'  # 11 characters, 19 bytes in UTF-8
    person = Person.objects.create(first_name=text, last_name='x')
    assert Person.objects.get(pk=person.pk).first_name == text
    assert Person.objects.filter(first_name__contains='€').exists()


def test_char_field_length_counts_utf8_bytes_and_overflow_is_rejected():
    # CharField is VARCHAR(max_length) CCSID 1208, whose length counts UTF-8 bytes (see README,
    # Limitations). A value that doesn't fit must fail loudly, never be truncated.
    with pytest.raises(DataError):
        with transaction.atomic():
            Person.objects.create(first_name='€' * 20, last_name='x')  # 20 characters, 60 bytes
    assert not Person.objects.filter(last_name='x').exists()


def test_positive_integer_check_constraint():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TypedValues.objects.create(positive=-1)


# Relations

def test_foreign_keys_and_related_queries():
    reporter = Reporter.objects.create(first_name='John', last_name='Smith')
    Article.objects.create(headline='First', pub_date=datetime.date(2024, 1, 1), reporter=reporter)
    Article.objects.create(headline='Second', pub_date=datetime.date(2024, 1, 2), reporter=reporter)

    article = Article.objects.select_related('reporter').get(headline='First')
    assert article.reporter.last_name == 'Smith'
    assert Article.objects.filter(reporter__first_name='John').count() == 2
    reporters = list(Reporter.objects.prefetch_related('article_set'))
    assert [len(r.article_set.all()) for r in reporters] == [2]


def test_foreign_key_to_non_primary_key_field():
    author = Author.objects.create(name='Tolkien')
    book = Book.objects.create(author=author)
    assert Book.objects.get(pk=book.pk).author.name == 'Tolkien'


def test_foreign_key_violation_raises_integrity_error():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Book.objects.create(author_id='nobody')


def test_unique_violation_raises_integrity_error():
    Author.objects.create(name='unique')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Author.objects.create(name='unique')


def test_many_to_many():
    first, second, third = Object.objects.bulk_create([Object(), Object(), Object()])
    first.related_objects.add(second, third)
    assert set(first.related_objects.values_list('pk', flat=True)) == {second.pk, third.pk}
    first.related_objects.remove(second)
    assert list(first.related_objects.all()) == [third]
    first.related_objects.clear()
    assert first.related_objects.count() == 0


def test_generic_foreign_key():
    person = Person.objects.create(first_name='Ada', last_name='Lovelace')
    tag = Tag.objects.create(name='t', content_object=person)
    assert Tag.objects.get(pk=tag.pk).content_object == person
    assert Tag.objects.filter(content_type=ContentType.objects.get_for_model(Person)).count() == 1


# Transactions and locking

def test_atomic_rollback_and_savepoints():
    with transaction.atomic():
        Person.objects.create(first_name='kept', last_name='x')
        try:
            with transaction.atomic():
                Person.objects.create(first_name='rolled-back', last_name='x')
                raise RuntimeError
        except RuntimeError:
            pass
    assert list(Person.objects.values_list('first_name', flat=True)) == ['kept']


def test_select_for_update():
    person = Person.objects.create(first_name='lock', last_name='x')
    with transaction.atomic():
        locked = Person.objects.select_for_update().get(pk=person.pk)
        assert locked.first_name == 'lock'
        assert list(Person.objects.select_for_update(skip_locked=True).filter(pk=person.pk)) == [locked]


def test_raw_sql_with_parameters():
    Person.objects.create(first_name='raw', last_name='x')
    people = list(Person.objects.raw('SELECT * FROM TESTS_PERSON WHERE FIRST_NAME = %s', ['raw']))
    assert [p.first_name for p in people] == ['raw']
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM TESTS_PERSON WHERE FIRST_NAME = %s', ['raw'])
        assert cursor.fetchone() == (1,)


# Composite primary keys

def test_composite_pk_create_and_read():
    line = OrderLine.objects.create(order_no=1, line_no=2, product='widget', quantity=3)
    assert line.pk == (1, 2)
    fetched = OrderLine.objects.get(pk=(1, 2))
    assert (fetched.product, fetched.quantity) == ('widget', 3)
    assert OrderLine.objects.get(order_no=1, line_no=2) == fetched


def test_composite_pk_update():
    line = OrderLine.objects.create(order_no=1, line_no=1, product='old')
    line.product = 'new'
    line.save()
    assert OrderLine.objects.get(pk=(1, 1)).product == 'new'
    assert OrderLine.objects.filter(pk=(1, 1)).update(quantity=9) == 1
    line.refresh_from_db()
    assert line.quantity == 9


def test_composite_pk_delete():
    OrderLine.objects.bulk_create([OrderLine(order_no=1, line_no=n, product='p') for n in range(1, 4)])
    OrderLine.objects.get(pk=(1, 2)).delete()
    assert list(OrderLine.objects.values_list('line_no', flat=True)) == [1, 3]
    deleted, _ = OrderLine.objects.filter(pk__in=[(1, 1), (1, 3)]).delete()
    assert deleted == 2
    assert not OrderLine.objects.exists()


def test_composite_pk_lookups():
    OrderLine.objects.bulk_create([
        OrderLine(order_no=order, line_no=line, product=f'{order}-{line}') for order in (1, 2) for line in (1, 2)
    ])
    assert OrderLine.objects.filter(pk__in=[(1, 2), (2, 1)]).count() == 2
    assert [line.pk for line in OrderLine.objects.filter(pk__gt=(1, 2))] == [(2, 1), (2, 2)]
    assert [line.pk for line in OrderLine.objects.filter(pk__lte=(1, 2))] == [(1, 1), (1, 2)]
    assert list(OrderLine.objects.order_by('-pk').values_list('pk', flat=True)[:1]) == [(2, 2)]


def test_composite_pk_bulk_operations():
    lines = OrderLine.objects.bulk_create([OrderLine(order_no=7, line_no=n, product='p') for n in range(1, 4)])
    for line in lines:
        line.quantity = line.line_no * 10
    assert OrderLine.objects.bulk_update(lines, ['quantity']) == 3
    assert list(OrderLine.objects.values_list('quantity', flat=True)) == [10, 20, 30]


def test_composite_pk_get_or_create_and_update_or_create():
    line, created = OrderLine.objects.get_or_create(order_no=5, line_no=1, defaults={'product': 'a'})
    assert created
    line, created = OrderLine.objects.update_or_create(order_no=5, line_no=1, defaults={'product': 'b'})
    assert not created and line.product == 'b'


def test_composite_pk_duplicate_raises_integrity_error():
    OrderLine.objects.create(order_no=3, line_no=1, product='a')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrderLine.objects.create(order_no=3, line_no=1, product='b')


def test_composite_pk_is_introspected():
    with connection.cursor() as cursor:
        assert connection.introspection.get_primary_key_columns(cursor, OrderLine._meta.db_table) == [
            'order_no', 'line_no']


# Flush (outside a transaction)

@pytest.mark.django_db(transaction=True)
def test_transactional_test_flushes_tables():
    # pytest-django flushes all tables after a transactional test, which exercises sql_flush.
    Author.objects.create(name='flushed')
    Book.objects.create(author_id='flushed')
    assert Book.objects.count() == 1
