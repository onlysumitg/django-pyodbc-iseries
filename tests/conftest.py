import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip tests that need the IBM i database when no connection is configured (see .env.example)."""
    if os.environ.get('TEST_SYSTEM_HOST'):
        return
    skip = pytest.mark.skip(reason='TEST_SYSTEM_HOST is not set; no IBM i system to test against')
    for item in items:
        if item.get_closest_marker('django_db') or {'db', 'transactional_db'} & set(item.fixturenames):
            item.add_marker(skip)
