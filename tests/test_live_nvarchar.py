"""
NVarCharField against a live IBM i system (skipped when not configured): max_length counts characters.
"""
import pytest
from django.db import DataError, transaction

from tests.models import UnicodeName

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('text', [
    '€' * 20,          # 20 characters, 60 bytes in UTF-8
    '日本語のテキスト' * 2 + '日本語の',  # 20 characters
    'José García',
    'a' * 20,
])
def test_holds_max_length_characters_of_any_script(text):
    row = UnicodeName.objects.create(name=text)
    assert UnicodeName.objects.get(pk=row.pk).name == text


def test_longer_values_are_rejected_not_truncated():
    with pytest.raises(DataError):
        with transaction.atomic():
            UnicodeName.objects.create(name='€' * 21)
    assert not UnicodeName.objects.exists()


def test_lookups_and_comparison_with_varchar_columns():
    UnicodeName.objects.create(name='Grüße 日本', code='Grüße 日本')
    UnicodeName.objects.create(name='other', code='x')
    assert UnicodeName.objects.filter(name__contains='日本').count() == 1
    assert UnicodeName.objects.filter(name__istartswith='GRÜ').count() == 1
    # NVARCHAR (CCSID 1200) and VARCHAR (CCSID 1208) values compare equal.
    from django.db.models import F
    assert UnicodeName.objects.filter(name=F('code')).count() == 1
