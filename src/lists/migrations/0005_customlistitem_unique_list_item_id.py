"""Add the unique constraint on list_item_id.

Kept separate from 0004: on PostgreSQL, adding a constraint in the same transaction
as the RunPython backfill fails with "pending trigger events".
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0004_customlistitem_list_item_id_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='customlistitem',
            constraint=models.UniqueConstraint(fields=('custom_list', 'list_item_id'), name='lists_customlistitem_unique_list_item_id'),
        ),
    ]
