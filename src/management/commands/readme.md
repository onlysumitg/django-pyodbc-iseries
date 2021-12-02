python manage.py inspectdb TableName > output.py


python manage.py inspectdb --database=[dbname] [table_name] > output.py

python manage.py inspectdb table1 table2 tableN > output.py


from django.core.management.commands.inspectdb import Command

command = Command()
command.execute(
database='default',
force_color=True,
no_color=False,
include_partitions=True,
include_views=True,
table=[
'auth_group',
'django_session'
]
)