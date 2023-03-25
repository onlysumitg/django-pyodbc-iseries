import os
import sys
from setuptools import setup, find_packages
#import get_suite

DESCRIPTION = "Django ISeries DB driver"
VERSION = '1.0.0'
LONG_DESCRIPTION = None
try:
    LONG_DESCRIPTION = open('README.md').read()
except:
    pass

requirements = [
 'pyodbc>=4.0.27', 
 'django>=2.2.0',
  
  'sqlparse',

]

# python setup.py publish
if sys.argv[-1] == 'publish':
    os.system("python setup.py sdist upload")
    sys.exit()
    
if sys.argv[-1] == 'egg_info':
    pass

CLASSIFIERS = [
    'Development Status :: 5 - Production/Stable',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: MIT License',
    'Operating System :: OS Independent',
    'Programming Language :: Python :: 3.8',
    'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.11',

    'Topic :: Software Development :: Libraries :: Python Modules',
    'Framework :: Django',
]

# https://packaging.python.org/en/latest/guides/distributing-packages-using-setuptools/

setup(
    name='django-pyodbc-iseries',
    version=VERSION,

    package_dir={'': 'src'},
    packages=find_packages(where='src'),

    include_package_data=True,
    author='Sumit',
    author_email='onlysumitg@gmail.com',
    url='',
    license='MIT',
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/x-rst',
    platforms=['any'],
    classifiers=CLASSIFIERS,
    install_requires=requirements,
    python_requires = '>=3.8',
)
