import os
import random
import string

SECRET_KEY = ''.join(random.choice(string.ascii_lowercase) for i in range(50))

USE_TZ = True
TIME_ZONE = 'UTC'

# Explicit, so Django 5.2 (default AutoField) and 6.x (default BigAutoField) build the same schema.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Connection settings come from the environment (pytest loads .env). They may be absent for the
# tests that never connect; tests that need the database will then fail to connect.
DATABASES = {
    'default':
        {
            'ENGINE': 'django_iseries',
            'NAME': 'iseries',  # arbitrary database name for db2 on iseries
            'HOST': os.environ.get('TEST_SYSTEM_HOST', ''),
            'USER': os.environ.get('TEST_SYSTEM_USERNAME', ''),
            'PASSWORD': os.environ.get('TEST_SYSTEM_PASSWORD', ''),
            # Tests run in the dedicated library from .env, which already exists and is journaled, so the
            # backend doesn't create (or drop) a TEST_<schema> library for them.
            'TEST': {'SCHEMA': os.environ.get('TEST_SYSTEM_SCHEMA', ''), 'CREATE_DB': False},
        },
    'other':
        {
            'ENGINE': 'django.db.backends.sqlite3',
        }
}

# Use a fast hasher to speed up tests.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.sites',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.admin.apps.SimpleAdminConfig',
    'django.contrib.staticfiles',

    'tests',
]

MIGRATION_MODULES = {
    # This lets us skip creating migrations for the test models as many of
    # them depend on one of the following contrib applications.
    'auth': None,
    'contenttypes': None,
    'sessions': None,
}
