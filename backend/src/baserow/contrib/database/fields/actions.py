import dataclasses
from copy import deepcopy
from typing import Optional, Tuple, List, Dict, Any, Set, Union

from django.contrib.auth.models import AbstractUser
from django.core.management.color import no_style
from django.db import connection
from django.db.models import ManyToManyField
from psycopg2 import sql

from baserow.contrib.database.db.schema import safe_django_schema_editor
from baserow.contrib.database.fields.handler import (
    FieldHandler,
)
from baserow.contrib.database.fields.models import Field, SpecificFieldForUpdate
from baserow.contrib.database.fields.registries import (
    field_type_registry,
)
from baserow.contrib.database.table.models import GeneratedTableModel, Table
from baserow.contrib.database.table.scopes import TableActionScopeType
from baserow.core.action.models import Action
from baserow.core.action.registries import ActionType, ActionScopeStr
from baserow.core.trash.handler import TrashHandler
from baserow.core.utils import extract_allowed

BackupData = Dict[str, Any]


class FieldDataBackupHandler:
    """
    Backs up an arbitrary Baserow field by getting their model fields and deciding how
    to backup based of it. Only the fields data (think cells) is backed up and no
    associated field meta-data is backed up by this class.

    The backup data is stored in the database and
    no serialization/deserialization of the data occurs. So it is fast but
    not suitable for actually backing up the data to prevent data loss, but instead
    useful for backing up the data due to Baserow actions to facilitate undoing them.

    If the model field is a many to many field then we backup by creating a duplicate
    m2m table and copying the data into it.

    Otherwise the field must be an actual column in the user table, so we duplicate
    the column and copy the data into it.

    Also knows how to restore from a backup and clean up any backups done by this
    class even if the Field/Table etc has been permanently deleted from Baserow.
    """

    @classmethod
    def backup_field_data(
        cls,
        field_to_backup: Field,
        identifier_to_backup_into: str,
    ) -> BackupData:
        """
        Backs up the provided field's data into a new column or table which will be
        named using the identifier_to_backup_into param.

        :param field_to_backup: A Baserow field that you want to backup the data for.
        :param identifier_to_backup_into: The name that will be used when creating
            the backup column or table.
        :return: A dictionary than can then be passed back into the other class methods
            to restore the backed up data or cleaned it up.
        """

        model = field_to_backup.table.get_model(
            field_ids=[],
            fields=[field_to_backup],
            add_dependencies=False,
        )

        model_field_to_backup = model._meta.get_field(field_to_backup.db_column)

        if isinstance(model_field_to_backup, ManyToManyField):
            through = model_field_to_backup.remote_field.through
            m2m_table_to_backup = through._meta.db_table
            cls._create_duplicate_m2m_table(
                model,
                m2m_model_field_to_duplicate=model_field_to_backup,
                new_m2m_table_name=identifier_to_backup_into,
            )
            cls._copy_m2m_data_between_tables(
                source_table=m2m_table_to_backup,
                target_table=identifier_to_backup_into,
                m2m_model_field=model_field_to_backup,
                through_model=through,
            )
            return {"backed_up_m2m_table_name": identifier_to_backup_into}
        else:
            table_name = model_field_to_backup.model._meta.db_table
            cls._create_duplicate_nullable_column(
                model,
                model_field_to_duplicate=model_field_to_backup,
                new_column_name=identifier_to_backup_into,
            )
            cls._copy_not_null_column_data(
                table_name,
                source_column=model_field_to_backup.column,
                target_column=identifier_to_backup_into,
            )
            return {
                "table_id_containing_backup_column": field_to_backup.table_id,
                "backed_up_column_name": identifier_to_backup_into,
            }

    @classmethod
    def restore_backup_data_into_field(
        cls,
        field_to_restore_backup_data_into: Field,
        backup_data: BackupData,
    ):
        """
        Given a dictionary generated by the backup_field_data this method copies the
        backed up data back into an existing Baserow field of the same type.
        """

        model = field_to_restore_backup_data_into.table.get_model(
            field_ids=[],
            fields=[field_to_restore_backup_data_into],
            add_dependencies=False,
        )
        model_field_to_restore_into = model._meta.get_field(
            field_to_restore_backup_data_into.db_column
        )
        if isinstance(model_field_to_restore_into, ManyToManyField):
            backed_up_m2m_table_name = backup_data["backed_up_m2m_table_name"]
            through = model_field_to_restore_into.remote_field.through
            target_m2m_table = through._meta.db_table

            cls._truncate_table(target_m2m_table)
            cls._copy_m2m_data_between_tables(
                source_table=backed_up_m2m_table_name,
                target_table=target_m2m_table,
                m2m_model_field=model_field_to_restore_into,
                through_model=through,
            )
            cls._drop_table(backed_up_m2m_table_name)
        else:
            backed_up_column_name = backup_data["backed_up_column_name"]
            table_name = model_field_to_restore_into.model._meta.db_table
            cls._copy_not_null_column_data(
                table_name,
                source_column=backed_up_column_name,
                target_column=model_field_to_restore_into.column,
            )
            cls._drop_column(table_name, backed_up_column_name)

    @classmethod
    def clean_up_backup_data(
        cls,
        backup_data: BackupData,
    ):
        """
        Given a dictionary generated by the backup_field_data this method deletes any
        backup data to reclaim space used.
        """

        if "backed_up_m2m_table_name" in backup_data:
            cls._drop_table(backup_data["backed_up_m2m_table_name"])
        else:
            try:
                table = Table.objects_and_trash.get(
                    id=backup_data["table_id_containing_backup_column"]
                )
                cls._drop_column(
                    table.get_database_table_name(),
                    backup_data["backed_up_column_name"],
                )
            except Table.DoesNotExist:
                # The table has already been permanently deleted by the trash system
                # so there is nothing for us to do.
                pass

    @staticmethod
    def _create_duplicate_m2m_table(
        model: GeneratedTableModel,
        m2m_model_field_to_duplicate: ManyToManyField,
        new_m2m_table_name: str,
    ):
        with safe_django_schema_editor() as schema_editor:
            # Create a duplicate m2m table to backup the data into.
            new_backup_table = deepcopy(m2m_model_field_to_duplicate)
            new_backup_table.remote_field.through._meta.db_table = new_m2m_table_name
            schema_editor.add_field(model, new_backup_table)

    @staticmethod
    def _truncate_table(target_table):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("TRUNCATE TABLE {target_table}").format(
                    target_table=sql.Identifier(target_table),
                )
            )

    @staticmethod
    def _drop_table(backup_name: str):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP TABLE {backup_table}").format(
                    backup_table=sql.Identifier(backup_name),
                )
            )

    @staticmethod
    def _copy_m2m_data_between_tables(
        source_table: str,
        target_table: str,
        m2m_model_field: ManyToManyField,
        through_model: GeneratedTableModel,
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                INSERT INTO {target_table} (id, {m2m_column}, {m2m_reverse_column})
                SELECT id, {m2m_column}, {m2m_reverse_column} FROM {source_table}
                """
                ).format(
                    source_table=sql.Identifier(source_table),
                    target_table=sql.Identifier(target_table),
                    m2m_column=sql.Identifier(m2m_model_field.m2m_column_name()),
                    m2m_reverse_column=sql.Identifier(
                        m2m_model_field.m2m_reverse_name()
                    ),
                )
            )
            # When the rows are inserted we keep the provide the old ids and because of
            # that the auto increment is still set at `1`. This needs to be set to the
            # maximum value because otherwise creating a new row could later fail.
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [through_model]
            )
            cursor.execute(sequence_sql[0])

    @staticmethod
    def _create_duplicate_nullable_column(
        model: GeneratedTableModel, model_field_to_duplicate, new_column_name: str
    ):
        with safe_django_schema_editor() as schema_editor:
            # Create a duplicate column to backup the data into.
            new_backup_model_field = deepcopy(model_field_to_duplicate)
            new_backup_model_field.column = new_column_name
            # It must be nullable so INSERT's into the table still work. If we restore
            # this backed up column back into a real column we won't copy over any
            # NULLs created by INSERTs.
            new_backup_model_field.null = True
            schema_editor.add_field(model, new_backup_model_field)

    @staticmethod
    def _copy_not_null_column_data(table_name, source_column, target_column):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {table_name} SET {target_column} = {source_column} "
                    "WHERE {source_column} IS NOT NULL"
                ).format(
                    table_name=sql.Identifier(table_name),
                    target_column=sql.Identifier(target_column),
                    source_column=sql.Identifier(source_column),
                )
            )

    @staticmethod
    def _drop_column(table_name: str, column_to_drop: str):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER TABLE {table_name} DROP COLUMN {column_to_drop}").format(
                    table_name=sql.Identifier(table_name),
                    column_to_drop=sql.Identifier(column_to_drop),
                )
            )


class UpdateFieldActionType(ActionType):
    type = "update_field"

    @dataclasses.dataclass
    class Params:
        field_id: int
        # We also need to persist the actual name of the database table in-case the
        # field itself is perm deleted by the time we clean up we can still find the
        # table and delete the column if need be.
        database_table_name: str

        previous_field_type: str
        previous_field_params: Dict[str, Any]

        backup_data: Optional[Dict[str, Any]]

    @classmethod
    def do(
        cls,
        user: AbstractUser,
        field: SpecificFieldForUpdate,
        new_type_name: Optional[str] = None,
        **kwargs,
    ) -> Tuple[Field, List[Field]]:

        """
        Updates the values and/or type of the given field. See
        baserow.contrib.database.fields.handler.FieldHandler.update_field for further
        details. Backs up the field attributes and data so undo will restore the
        original field and its data. Redo reapplies the update.

        :param user: The user on whose behalf the table is updated.
        :param field: The field instance that needs to be updated.
        :param new_type_name: If the type needs to be changed it can be provided here.
        :return: The updated field instance and any
            updated fields as a result of updated the field are returned in a list
            as the second tuple value.
        """

        from_field_type = field_type_registry.get_by_model(field)
        from_field_type_name = from_field_type.type
        to_field_type_name = new_type_name or from_field_type_name
        to_field_type = field_type_registry.get(to_field_type_name)

        allowed_fields = ["name"] + to_field_type.allowed_fields
        allowed_field_values = extract_allowed(kwargs, allowed_fields)

        updated_field_attrs = set(allowed_field_values.keys())
        original_exported_values = cls._get_prepared_field_attrs(
            field, updated_field_attrs, to_field_type_name
        )

        # We initially create the action with blank params so we have an action id
        # to use when naming a possible backup field/table.
        action = cls.register_action(user, {}, cls.scope(field.table_id))

        optional_backup_data = cls._backup_field_if_required(
            field, allowed_field_values, action, to_field_type_name, False
        )

        field, updated_fields = FieldHandler().update_field(
            user,
            field,
            new_type_name,
            return_updated_fields=True,
            **allowed_field_values,
        )

        action.params = cls.Params(
            field_id=field.id,
            database_table_name=field.table.get_database_table_name(),
            previous_field_type=from_field_type_name,
            previous_field_params=original_exported_values,
            backup_data=optional_backup_data,
        )
        action.save()

        return field, updated_fields

    @classmethod
    def scope(cls, table_id) -> ActionScopeStr:
        return TableActionScopeType.value(table_id)

    @classmethod
    def undo(
        cls,
        user: AbstractUser,
        params: Params,
        action_being_undone: Action,
    ):
        cls._backup_field_then_update_back_to_previous_backup(
            user,
            action_being_undone,
            params,
            for_undo=True,
        )

    @classmethod
    def redo(cls, user: AbstractUser, params: Params, action_being_redone: Action):
        cls._backup_field_then_update_back_to_previous_backup(
            user,
            action_being_redone,
            params,
            for_undo=False,
        )

    @classmethod
    def clean_up_any_extra_action_data(cls, action_being_cleaned_up: Action):
        params = cls.Params(**action_being_cleaned_up.params)
        if params.backup_data is not None:
            FieldDataBackupHandler.clean_up_backup_data(params.backup_data)

    @classmethod
    def _backup_field_if_required(
        cls,
        original_field: Field,
        allowed_new_field_attrs: Dict[str, Any],
        action: Action,
        to_field_type_name: str,
        for_undo: bool,
    ) -> Optional[BackupData]:
        """
        Performs a backup if needed and returns a dictionary of backup data which can
        be then used with the FieldDataBackupHandler to restore a backup or clean up
        the backed up data.
        """

        if cls._should_backup_field(
            original_field, to_field_type_name, allowed_new_field_attrs
        ):
            backup_data = FieldDataBackupHandler.backup_field_data(
                original_field,
                identifier_to_backup_into=cls._get_backup_identifier(
                    action, original_field.id, for_undo=for_undo
                ),
            )
        else:
            backup_data = None
        return backup_data

    @classmethod
    def _should_backup_field(
        cls,
        original_field: Field,
        to_field_type_name: str,
        allowed_new_field_attrs: Dict[str, Any],
    ) -> bool:
        """
        Calculates whether the field should be backed up given its original instance,
        the type it is being converted to and any attributes which are being updated.
        """

        from_field_type = field_type_registry.get_by_model(original_field)
        from_field_type_name = from_field_type.type

        field_type_changed = to_field_type_name != from_field_type_name
        only_name_changed = allowed_new_field_attrs.keys() == {"name"}

        if from_field_type.field_data_is_derived_from_attrs:
            # If the field we are converting from can reconstruct its data just from its
            # attributes we never need to backup any data.
            return False

        return field_type_changed or (
            from_field_type.should_backup_field_data_for_same_type_update(
                original_field, allowed_new_field_attrs
            )
            and not only_name_changed
        )

    @classmethod
    def _get_prepared_field_attrs(
        cls, field: Field, field_attrs_being_updated: Set[str], to_field_type_name: str
    ):
        """
        Prepare values to be saved depending on whether the field type has changed
        or not.

        If we aren't changing field type then only save the attributes which
        the user has changed.

        Otherwise, if we have changed field type then we need to save all the original
        field types attributes. However we don't want to save the only shared
        field attr "name" if it hasn't changed so we don't undo other users name
        changes.
        """

        from_field_type = field_type_registry.get_by_model(field)
        from_field_type_name = from_field_type.type

        original_exported_values = from_field_type.export_prepared_values(field)
        if to_field_type_name == from_field_type_name:
            exported_field_attrs_which_havent_changed = (
                original_exported_values.keys() - field_attrs_being_updated
            )
            for key in exported_field_attrs_which_havent_changed:
                original_exported_values.pop(key)
        else:
            if "name" not in field_attrs_being_updated:
                original_exported_values.pop("name")
        return original_exported_values

    @classmethod
    def _get_backup_identifier(
        cls, action: Action, field_id: int, for_undo: bool
    ) -> str:
        """
        Returns a column/table name unique to this action and field which can be
        used to safely store backup data in the database.
        """

        base_name = f"field_{field_id}_backup_{action.id}"
        if for_undo:
            # When undoing we need to backup into a different column/table so we
            # don't accidentally overwrite the data we are about to restore using.
            return base_name + "_undo"
        else:
            return base_name

    @classmethod
    def _backup_field_then_update_back_to_previous_backup(
        cls,
        user: AbstractUser,
        action: Action,
        params: Params,
        for_undo: bool,
    ):
        new_field_attributes = deepcopy(params.previous_field_params)
        to_field_type_name = params.previous_field_type

        handler = FieldHandler()
        field = handler.get_specific_field_for_update(params.field_id)

        updated_field_attrs = set(new_field_attributes.keys())
        previous_field_params = cls._get_prepared_field_attrs(
            field, updated_field_attrs, to_field_type_name
        )
        from_field_type = field_type_registry.get_by_model(field)
        from_field_type_name = from_field_type.type

        optional_backup_data = cls._backup_field_if_required(
            field, new_field_attributes, action, to_field_type_name, for_undo
        )

        def after_field_schema_change_callback(
            field_after_schema_change: SpecificFieldForUpdate,
        ):
            if params.backup_data:
                # We have to restore the field data immediately after the schema change
                # as the dependant field updates performed by `update_field` need the
                # correct cell data in place and ready.
                # E.g. If the field we are undoing has a formula field which depends
                # on it we have to copy back in the cell values before that formula
                # field updates its own cells.
                FieldDataBackupHandler.restore_backup_data_into_field(
                    field_after_schema_change, params.backup_data
                )

        # If when undoing/redoing there is now a new field with the same name we don't
        # want to fail and throw away all the users lost data. Instead we just find
        # a new free field name starting by adding the following postfix on and use
        # that instead.
        collision_postfix = "(From undo)" if for_undo else "(From redo)"
        handler.update_field(
            user,
            field,
            new_type_name=to_field_type_name,
            postfix_to_fix_name_collisions=collision_postfix,
            return_updated_fields=True,
            after_schema_change_callback=after_field_schema_change_callback,
            **new_field_attributes,
        )

        params.backup_data = optional_backup_data
        params.previous_field_type = from_field_type_name
        params.previous_field_params = previous_field_params
        action.params = params


class CreateFieldActionType(ActionType):
    type = "create_field"

    @dataclasses.dataclass
    class Params:
        field_id: int

    @classmethod
    def do(
        cls,
        user: AbstractUser,
        table: Table,
        type_name: str,
        primary=False,
        return_updated_fields=False,
        **kwargs,
    ) -> Union[Field, Tuple[Field, List[Field]]]:
        """
        Creates a new field with the given type for a table.
        See baserow.contrib.database.fields.handler.FieldHandler.create_field()
        for more information.
        Undoing this action will delete the field.
        Redoing this action will restore the field.

        :param user: The user on whose behalf the field is created.
        :param table: The table that the field belongs to.
        :param type_name: The type name of the field. Available types can be found in
            the field_type_registry.
        :param primary: Every table needs at least a primary field which cannot be
            deleted and is a representation of the whole row.
        :param return_updated_fields: When True any other fields who changed as a
            result of this field creation are returned with their new field instances.
        :param kwargs: The field values that need to be set upon creation.
        :type kwargs: object
        :return: The created field instance. If return_updated_field is set then any
            updated fields as a result of creating the field are returned in a list
            as a second tuple value.
        """

        result = FieldHandler().create_field(
            user,
            table,
            type_name,
            primary=primary,
            return_updated_fields=return_updated_fields,
            **kwargs,
        )

        if return_updated_fields:
            field, updated_fields = result
        else:
            field = result
            updated_fields = None

        cls.register_action(
            user=user, params=cls.Params(field_id=field.id), scope=cls.scope(table.id)
        )

        return (field, updated_fields) if return_updated_fields else field

    @classmethod
    def scope(cls, table_id) -> ActionScopeStr:
        return TableActionScopeType.value(table_id)

    @classmethod
    def undo(cls, user: AbstractUser, params: Params, action_being_undone: Action):
        field = FieldHandler().get_field(params.field_id)
        FieldHandler().delete_field(user, field)

    @classmethod
    def redo(cls, user: AbstractUser, params: Params, action_being_redone: Action):
        TrashHandler().restore_item(user, "field", params.field_id)


class DeleteFieldActionType(ActionType):
    type = "delete_field"

    @dataclasses.dataclass
    class Params:
        field_id: int

    @classmethod
    def do(
        cls,
        user: AbstractUser,
        field: Field,
    ) -> List[Field]:
        """
        Deletes an existing field if it is not a primary field.
        See baserow.contrib.database.fields.handler.FieldHandler.delete_field()
        for more information.
        Undoing this action will restore the field.
        Redoing this action will delete the field.

        :param user: The user on whose behalf the table is created.
        :param field: The field instance that needs to be deleted.
        :return: The related updated fields.
        """

        result = FieldHandler().delete_field(user, field)

        cls.register_action(
            user=user,
            params=cls.Params(
                field.id,
            ),
            scope=cls.scope(field.table_id),
        )

        return result

    @classmethod
    def scope(cls, table_id) -> ActionScopeStr:
        return TableActionScopeType.value(table_id)

    @classmethod
    def undo(cls, user: AbstractUser, params: Params, action_being_undone: Action):
        TrashHandler().restore_item(user, "field", params.field_id)

    @classmethod
    def redo(cls, user: AbstractUser, params: Params, action_being_redone: Action):
        field = FieldHandler().get_field(params.field_id)
        FieldHandler().delete_field(
            user,
            field,
        )