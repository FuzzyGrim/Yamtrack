"""Tests for the shared idempotent migration operations.

The schema tests run a real schema editor against throwaway tables, so they
exercise whichever backend the suite runs on. Run them on PostgreSQL too::

    scripts/test_postgres.sh app.tests.test_migration_operations
"""

from __future__ import annotations

from django.db import DatabaseError, connection, migrations, models
from django.db.migrations.optimizer import MigrationOptimizer
from django.db.migrations.state import ProjectState
from django.db.migrations.writer import MigrationWriter
from django.test import SimpleTestCase, TransactionTestCase

from app import migration_operations as ops
from app.migration_operations import (
    AddConstraintIfNotExists,
    AddFieldIfNotExists,
    AddIndexIfNotExists,
    CreateModelIfNotExists,
    RemoveConstraintIfExists,
    RemoveFieldIfExists,
    RemoveIndexIfExists,
)

APP_LABEL = "migops"
TABLE = f"{APP_LABEL}_widget"


def _check(name="widget_size_positive"):
    return models.CheckConstraint(condition=models.Q(size__gte=0), name=name)


def _partial_unique(name="widget_label_unique_when_named"):
    # Stored as a partial index on PostgreSQL, so pg_constraint cannot see it.
    return models.UniqueConstraint(
        fields=["label"], condition=~models.Q(label=""), name=name
    )


def _create_widget(operation_class=migrations.CreateModel):
    return operation_class(
        "Widget",
        fields=[
            ("id", models.AutoField(primary_key=True)),
            ("label", models.CharField(max_length=20, default="")),
            ("size", models.IntegerField(default=0)),
        ],
    )


def _size_index(name="widget_size_idx"):
    return models.Index(fields=["size"], name=name)


class SchemaOperationTests(TransactionTestCase):
    """Apply each operation through a real schema editor, twice where useful."""

    # Nothing here touches the project's own tables; skip the flush.
    available_apps: list[str] = []

    def setUp(self):
        self.state = self._apply(ProjectState(), _create_widget())

    def tearDown(self):
        with connection.schema_editor() as editor:
            editor.execute(
                editor.sql_delete_table % {"table": editor.quote_name(TABLE)}
            )

    def _apply(self, state, operation):
        new_state = state.clone()
        operation.state_forwards(APP_LABEL, new_state)
        with connection.schema_editor() as editor:
            operation.database_forwards(APP_LABEL, editor, state, new_state)
        return new_state

    def _unapply(self, state_before, operation):
        state_after = state_before.clone()
        operation.state_forwards(APP_LABEL, state_after)
        with connection.schema_editor() as editor:
            operation.database_backwards(APP_LABEL, editor, state_after, state_before)

    def _has_column(self, column):
        with connection.schema_editor() as editor:
            return ops.column_exists(editor, TABLE, column)

    def _has_constraint(self, name):
        with connection.schema_editor() as editor:
            return ops.constraint_exists(editor, TABLE, name)

    def test_add_field_twice_is_a_no_op_the_second_time(self):
        operation = AddFieldIfNotExists(
            "widget", "colour", models.CharField(max_length=10, default="")
        )
        self._apply(self.state, operation)
        self.assertTrue(self._has_column("colour"))

        # Second run starts from the old state, as a drifted database would.
        self._apply(self.state, operation)
        self.assertTrue(self._has_column("colour"))

    def test_plain_add_index_twice_fails_so_the_guard_is_doing_the_work(self):
        # SQLite rebuilds the table for a plain AddField, so it hides a
        # duplicate; CREATE INDEX fails on both backends.
        operation = migrations.AddIndex("widget", _size_index())
        self._apply(self.state, operation)
        with self.assertRaises(DatabaseError):
            self._apply(self.state, operation)

    def test_add_field_reverse_skips_a_column_already_gone(self):
        operation = AddFieldIfNotExists(
            "widget", "colour", models.CharField(max_length=10, default="")
        )
        self._unapply(self.state, operation)
        self.assertFalse(self._has_column("colour"))

    def test_remove_field_when_absent_does_nothing(self):
        state = self._apply(
            self.state,
            AddFieldIfNotExists("widget", "colour", models.CharField(max_length=10)),
        )
        operation = RemoveFieldIfExists("widget", "colour")
        self._apply(state, operation)
        self.assertFalse(self._has_column("colour"))
        self._apply(state, operation)
        self.assertFalse(self._has_column("colour"))

    def test_add_check_constraint_twice(self):
        operation = AddConstraintIfNotExists("widget", _check())
        self._apply(self.state, operation)
        self._apply(self.state, operation)
        self.assertTrue(self._has_constraint("widget_size_positive"))

    def test_add_partial_unique_constraint_twice(self):
        operation = AddConstraintIfNotExists("widget", _partial_unique())
        self._apply(self.state, operation)
        self._apply(self.state, operation)
        self.assertTrue(self._has_constraint("widget_label_unique_when_named"))

    def test_remove_constraint_removes_when_present_and_skips_when_absent(self):
        state = self._apply(self.state, AddConstraintIfNotExists("widget", _check()))
        operation = RemoveConstraintIfExists("widget", "widget_size_positive")
        self._apply(state, operation)
        self.assertFalse(self._has_constraint("widget_size_positive"))
        self._apply(state, operation)
        self.assertFalse(self._has_constraint("widget_size_positive"))

    def test_remove_constraint_never_added_does_nothing(self):
        state = self.state.clone()
        state.add_constraint(APP_LABEL, "widget", _check())
        self._apply(state, RemoveConstraintIfExists("widget", "widget_size_positive"))
        self.assertFalse(self._has_constraint("widget_size_positive"))

    def test_add_index_twice_and_remove_index_twice(self):
        add = AddIndexIfNotExists("widget", _size_index())
        state = self._apply(self.state, add)
        self._apply(self.state, add)
        self.assertTrue(self._has_constraint("widget_size_idx"))

        remove = RemoveIndexIfExists("widget", "widget_size_idx")
        self._apply(state, remove)
        self._apply(state, remove)
        self.assertFalse(self._has_constraint("widget_size_idx"))

    def test_create_model_when_table_exists_does_nothing(self):
        self._apply(ProjectState(), _create_widget(CreateModelIfNotExists))
        with connection.cursor() as cursor:
            self.assertIn(TABLE, connection.introspection.table_names(cursor))

    def test_checks_on_a_missing_table_report_absent(self):
        with connection.schema_editor() as editor:
            self.assertFalse(ops.column_exists(editor, "migops_missing", "id"))
            self.assertFalse(ops.constraint_exists(editor, "migops_missing", "x"))


def _optimize(operations):
    return MigrationOptimizer().optimize(operations, APP_LABEL)


def _shape(operations):
    """Return (class name, deconstructed kwargs) with the wrapper suffix removed."""
    shape = []
    for operation in operations:
        name, _args, kwargs = operation.deconstruct()
        base = name.removesuffix("IfNotExists").removesuffix("IfExists")
        shape.append((base, sorted(kwargs)))
    return shape


class ReduceTests(SimpleTestCase):
    """The optimizer collapses shared operations by Django's own rules."""

    def test_add_then_remove_constraint_collapses(self):
        self.assertEqual(
            _optimize(
                [
                    AddConstraintIfNotExists("widget", _check()),
                    RemoveConstraintIfExists("widget", "widget_size_positive"),
                ]
            ),
            [],
        )

    def test_add_then_plain_remove_constraint_collapses(self):
        self.assertEqual(
            _optimize(
                [
                    AddConstraintIfNotExists("widget", _check()),
                    migrations.RemoveConstraint("widget", "widget_size_positive"),
                ]
            ),
            [],
        )

    def test_non_matching_constraint_pairs_stay(self):
        for remove in (
            RemoveConstraintIfExists("widget", "some_other_name"),
            RemoveConstraintIfExists("gadget", "widget_size_positive"),
        ):
            with self.subTest(remove=remove):
                result = _optimize(
                    [AddConstraintIfNotExists("widget", _check()), remove]
                )
                self.assertEqual(len(result), 2)

    def test_remove_then_add_constraint_is_kept_like_django(self):
        # Django never cancels remove-then-add: the new definition may differ.
        sequence = [
            RemoveConstraintIfExists("widget", "widget_size_positive"),
            AddConstraintIfNotExists("widget", _check()),
        ]
        plain = [
            migrations.RemoveConstraint("widget", "widget_size_positive"),
            migrations.AddConstraint("widget", _check()),
        ]
        self.assertEqual(len(_optimize(sequence)), 2)
        self.assertEqual(_shape(_optimize(sequence)), _shape(_optimize(plain)))

    def test_add_remove_add_remove_collapses_to_nothing(self):
        result = _optimize(
            [
                AddConstraintIfNotExists("widget", _check()),
                RemoveConstraintIfExists("widget", "widget_size_positive"),
                AddConstraintIfNotExists("widget", _check()),
                RemoveConstraintIfExists("widget", "widget_size_positive"),
            ]
        )
        self.assertEqual(result, [])

    def test_intervening_operations_follow_djangos_rules(self):
        """Whatever Django does for its own classes, the shared ones do too."""
        field = models.IntegerField(default=0)
        between_cases = {
            "field on same model": (
                AddFieldIfNotExists("widget", "weight", field),
                migrations.AddField("widget", "weight", field),
            ),
            "constraint on same model": (
                AddConstraintIfNotExists("widget", _check("other")),
                migrations.AddConstraint("widget", _check("other")),
            ),
            "other model": (
                AddFieldIfNotExists("gadget", "weight", field),
                migrations.AddField("gadget", "weight", field),
            ),
        }
        for label, (shared_between, plain_between) in between_cases.items():
            with self.subTest(label):
                shared = _optimize(
                    [
                        AddConstraintIfNotExists("widget", _check()),
                        shared_between,
                        RemoveConstraintIfExists("widget", "widget_size_positive"),
                    ]
                )
                plain = _optimize(
                    [
                        migrations.AddConstraint("widget", _check()),
                        plain_between,
                        migrations.RemoveConstraint("widget", "widget_size_positive"),
                    ]
                )
                self.assertEqual(_shape(shared), _shape(plain))

    def test_add_field_then_remove_field_collapses(self):
        field = models.IntegerField(default=0)
        self.assertEqual(
            _optimize(
                [
                    AddFieldIfNotExists("widget", "weight", field),
                    RemoveFieldIfExists("widget", "weight"),
                ]
            ),
            [],
        )

    def test_add_field_then_alter_field_stays_idempotent(self):
        altered = models.IntegerField(default=5)
        result = _optimize(
            [
                AddFieldIfNotExists("widget", "weight", models.IntegerField()),
                migrations.AlterField("widget", "weight", altered),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AddFieldIfNotExists)
        self.assertIs(result[0].field, altered)

    def test_add_field_then_rename_is_not_merged(self):
        result = _optimize(
            [
                AddFieldIfNotExists("widget", "weight", models.IntegerField()),
                migrations.RenameField("widget", "weight", "mass"),
            ]
        )
        self.assertEqual(len(result), 2)

    def test_add_constraint_then_alter_constraint_stays_idempotent(self):
        result = _optimize(
            [
                AddConstraintIfNotExists("widget", _check()),
                migrations.AlterConstraint("widget", "widget_size_positive", _check()),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AddConstraintIfNotExists)

    def test_add_index_then_remove_index_collapses(self):
        self.assertEqual(
            _optimize(
                [
                    AddIndexIfNotExists("widget", _size_index()),
                    RemoveIndexIfExists("widget", "widget_size_idx"),
                ]
            ),
            [],
        )

    def test_add_index_then_rename_stays_idempotent(self):
        result = _optimize(
            [
                AddIndexIfNotExists("widget", _size_index()),
                migrations.RenameIndex(
                    "widget", new_name="widget_size_ix", old_name="widget_size_idx"
                ),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AddIndexIfNotExists)
        self.assertEqual(result[0].index.name, "widget_size_ix")

    def test_create_model_if_not_exists_does_not_absorb_later_fields(self):
        result = _optimize(
            [
                _create_widget(CreateModelIfNotExists),
                migrations.AddField("widget", "weight", models.IntegerField()),
            ]
        )
        self.assertEqual(len(result), 2)

    def test_create_model_if_not_exists_then_delete_collapses(self):
        self.assertEqual(
            _optimize(
                [
                    _create_widget(CreateModelIfNotExists),
                    migrations.DeleteModel("widget"),
                ]
            ),
            [],
        )


class SerializationTests(SimpleTestCase):
    """Migrations that use the shared operations serialize to importable code."""

    def test_writer_imports_the_shared_module(self):
        migration = type(
            "Migration",
            (migrations.Migration,),
            {
                "dependencies": [],
                "operations": [
                    AddFieldIfNotExists("widget", "weight", models.IntegerField()),
                    AddConstraintIfNotExists("widget", _check()),
                    RemoveConstraintIfExists("widget", "widget_size_positive"),
                    AddIndexIfNotExists("widget", _size_index()),
                    RemoveIndexIfExists("widget", "widget_size_idx"),
                    RemoveFieldIfExists("widget", "weight"),
                    _create_widget(CreateModelIfNotExists),
                ],
            },
        )("0001_scratch", APP_LABEL)

        source = MigrationWriter(migration).as_string()

        self.assertIn("import app.migration_operations", source)
        namespace: dict = {}
        exec(compile(source, "0001_scratch.py", "exec"), namespace)  # noqa: S102
        rebuilt = namespace["Migration"].operations
        self.assertEqual(
            [type(operation) for operation in rebuilt],
            [type(operation) for operation in migration.operations],
        )

    def test_every_shared_name_is_exported(self):
        for name in ops.SHARED_OPERATION_NAMES:
            with self.subTest(name=name):
                self.assertTrue(
                    issubclass(getattr(ops, name), migrations.operations.base.Operation)
                )
