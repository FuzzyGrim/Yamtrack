"""Idempotent migration operations, shared by every Floppy app.

Import these in new migrations instead of defining a copy inline::

    from app.migration_operations import AddFieldIfNotExists

Each operation is Django's own operation with one difference: before it
changes the database it looks at what is already there, and it skips the
change when the database already matches. That lets a migration run safely
against a database that drifted (a column added by hand, a constraint an
earlier release left behind, a restore from a different version).

Why one shared module matters:

* ``check_migration_hygiene --strict`` rejects a new migration that defines
  one of these classes inline. 102 shipped migrations already do; they are
  listed in ``inline_migration_operations_allowlist.txt`` and never change.
* ``squashmigrations`` writes ``import app.migration_operations`` for these
  classes. A class defined inside ``users/migrations/0038_x.py`` cannot be
  imported that way at all, because the module name starts with a digit.
* ``reduce()`` keeps the idempotent class when Django's optimizer merges two
  operations, so a squash never quietly turns an idempotent add into a plain
  one. Django's base classes would return a plain ``AddField`` there.

Existence checks use Django's introspection scoped to the table, on SQLite
and PostgreSQL alike. Most inline copies only checked on PostgreSQL, by
constraint name across the whole schema, and on SQLite assumed the
constraint was absent. The shared version answers the same question
correctly on both backends, which also covers PostgreSQL partial unique
constraints (stored as indexes, invisible to ``pg_constraint``).
"""

from __future__ import annotations

from django.db import migrations
from django.db.migrations.operations.base import Operation

__all__ = [
    "AddConstraintIfNotExists",
    "AddFieldIfNotExists",
    "AddIndexIfNotExists",
    "CreateModelIfNotExists",
    "RemoveConstraintIfExists",
    "RemoveFieldIfExists",
    "RemoveIndexIfExists",
]

# Names the hygiene check refuses to see defined inline in a new migration.
SHARED_OPERATION_NAMES = frozenset(__all__)


def table_exists(schema_editor, table_name: str) -> bool:
    """Return whether ``table_name`` exists in the current schema."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        return table_name in connection.introspection.table_names(cursor)


def column_exists(schema_editor, table_name: str, column_name: str) -> bool:
    """Return whether ``table_name`` has ``column_name``.

    A missing table counts as a missing column. The table check runs first so
    PostgreSQL never sees a failing query that would abort the transaction.
    """
    if not table_exists(schema_editor, table_name):
        return False
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return column_name in {column.name for column in description}


def constraint_exists(schema_editor, table_name: str, name: str) -> bool:
    """Return whether ``table_name`` has a constraint or index called ``name``.

    Django's introspection reports constraints and indexes together, so this
    also answers for indexes and for unique constraints stored as indexes.
    """
    if not table_exists(schema_editor, table_name):
        return False
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)
    return name in constraints


def _field_is_present(schema_editor, model, field) -> bool:
    if field.many_to_many:
        through = field.remote_field.through
        return table_exists(schema_editor, through._meta.db_table)
    if field.column is None:
        # A field with no column (for example a GenericRelation) has nothing
        # to check; let Django decide, as it would without the wrapper.
        return False
    return column_exists(schema_editor, model._meta.db_table, field.column)


class AddFieldIfNotExists(migrations.AddField):
    """``AddField`` that skips a column (or many-to-many table) already there."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Add the field unless the database already has it."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if _field_is_present(schema_editor, model, model._meta.get_field(self.name)):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Remove the field on reverse, unless it is already gone."""
        model = from_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        if not _field_is_present(schema_editor, model, field):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)

    def reduce(self, operation, app_label):
        """Merge like ``AddField``, but keep the result idempotent.

        A rename is not merged: if the old column already exists, the
        unmerged pair renames it while a merged add would create a second,
        empty column.
        """
        if isinstance(operation, migrations.AlterField) and (
            self.is_same_field_operation(operation)
        ):
            return [
                self.__class__(
                    model_name=self.model_name,
                    name=operation.name,
                    field=operation.field,
                )
            ]
        if isinstance(operation, migrations.RenameField) and (
            self.is_same_field_operation(operation)
        ):
            return False
        return super().reduce(operation, app_label)


class RemoveFieldIfExists(migrations.RemoveField):
    """``RemoveField`` that does nothing when the column is already gone."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Remove the field if the database still has it."""
        model = from_state.apps.get_model(app_label, self.model_name)
        if not _field_is_present(
            schema_editor, model, model._meta.get_field(self.name)
        ):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Re-add the field on reverse, unless it is already there."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if _field_is_present(schema_editor, model, model._meta.get_field(self.name)):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class AddConstraintIfNotExists(migrations.AddConstraint):
    """``AddConstraint`` that skips a constraint name the table already has."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Add the constraint unless the table already has one by that name."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if constraint_exists(schema_editor, model._meta.db_table, self.constraint.name):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Drop the constraint on reverse, unless it is already gone."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if not constraint_exists(
            schema_editor, model._meta.db_table, self.constraint.name
        ):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)

    def reduce(self, operation, app_label):
        """Merge like ``AddConstraint``, but keep the result idempotent.

        A matching ``RemoveConstraint`` (or ``RemoveConstraintIfExists``)
        still cancels the pair, exactly as Django's own classes do.
        """
        if (
            isinstance(operation, migrations.AlterConstraint)
            and self.model_name_lower == operation.model_name_lower
            and self.constraint.name == operation.name
        ):
            return [self.__class__(self.model_name, operation.constraint)]
        return super().reduce(operation, app_label)


class RemoveConstraintIfExists(migrations.RemoveConstraint):
    """``RemoveConstraint`` that does nothing when the constraint is gone."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Drop the constraint if the table still has it."""
        model = from_state.apps.get_model(app_label, self.model_name)
        if not constraint_exists(schema_editor, model._meta.db_table, self.name):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Re-add the constraint on reverse, unless it is already there."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if constraint_exists(schema_editor, model._meta.db_table, self.name):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class AddIndexIfNotExists(migrations.AddIndex):
    """``AddIndex`` that skips an index name the table already has.

    Django's ``AddIndex.reduce`` already rebuilds with ``self.__class__`` on
    a rename and cancels against a matching ``RemoveIndex``, so it is
    inherited unchanged.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Add the index unless the table already has one by that name."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if constraint_exists(schema_editor, model._meta.db_table, self.index.name):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Drop the index on reverse, unless it is already gone."""
        model = from_state.apps.get_model(app_label, self.model_name)
        if not constraint_exists(schema_editor, model._meta.db_table, self.index.name):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class RemoveIndexIfExists(migrations.RemoveIndex):
    """``RemoveIndex`` that does nothing when the index is already gone."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Drop the index if the table still has it."""
        model = from_state.apps.get_model(app_label, self.model_name)
        if not constraint_exists(schema_editor, model._meta.db_table, self.name):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Re-add the index on reverse, unless it is already there."""
        model = to_state.apps.get_model(app_label, self.model_name)
        if constraint_exists(schema_editor, model._meta.db_table, self.name):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class CreateModelIfNotExists(migrations.CreateModel):
    """``CreateModel`` that skips a table that already exists."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Create the table unless it already exists."""
        model = to_state.apps.get_model(app_label, self.name)
        if table_exists(schema_editor, model._meta.db_table):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Drop the table on reverse, unless it is already gone."""
        model = from_state.apps.get_model(app_label, self.name)
        if not table_exists(schema_editor, model._meta.db_table):
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)

    def reduce(self, operation, app_label):
        """Cancel against a matching ``DeleteModel``, and absorb nothing else.

        ``CreateModel`` folds later field, option and constraint changes into
        itself. Here that would be unsafe: when the table already exists the
        whole merged operation is skipped, so a column a later ``AddField``
        would have added is silently lost.
        """
        if isinstance(operation, migrations.DeleteModel) and (
            self.name_lower == operation.name_lower
            and not self.options.get("proxy", False)
        ):
            return []
        return Operation.reduce(self, operation, app_label) or self.can_reduce_through(
            operation, app_label
        )
