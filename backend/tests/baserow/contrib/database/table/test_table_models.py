import pytest
from decimal import Decimal

from unittest.mock import MagicMock

from django.db import models

from baserow.contrib.database.table.models import Table
from baserow.contrib.database.fields.exceptions import (
    OrderByFieldNotPossible, OrderByFieldNotFound, FilterFieldNotFound
)
from baserow.contrib.database.views.exceptions import (
    ViewFilterTypeNotAllowedForField, ViewFilterTypeDoesNotExist
)


@pytest.mark.django_db
def test_group_user_get_next_order(data_fixture):
    database = data_fixture.create_database_application()
    database_2 = data_fixture.create_database_application()
    data_fixture.create_database_table(order=1, database=database)
    data_fixture.create_database_table(order=2, database=database)
    data_fixture.create_database_table(order=10, database=database_2)

    assert Table.get_last_order(database) == 3
    assert Table.get_last_order(database_2) == 11


@pytest.mark.django_db
def test_get_table_model(data_fixture):
    default_model_fields_count = 3
    table = data_fixture.create_database_table(name='Cars')
    text_field = data_fixture.create_text_field(table=table, order=0, name='Color',
                                                text_default='white')
    number_field = data_fixture.create_number_field(table=table, order=1,
                                                    name='Horsepower')
    boolean_field = data_fixture.create_boolean_field(table=table, order=2,
                                                      name='For sale')

    model = table.get_model(attribute_names=True)
    assert model.__name__ == f'Table{table.id}Model'
    assert model._generated_table_model
    assert model._meta.db_table == f'database_table_{table.id}'
    assert len(model._meta.get_fields()) == 4 + default_model_fields_count

    color_field = model._meta.get_field('color')
    horsepower_field = model._meta.get_field('horsepower')
    for_sale_field = model._meta.get_field('for_sale')

    assert isinstance(color_field, models.TextField)
    assert color_field.verbose_name == 'Color'
    assert color_field.db_column == f'field_{text_field.id}'
    assert color_field.default == 'white'
    assert color_field.null

    assert isinstance(horsepower_field, models.DecimalField)
    assert horsepower_field.verbose_name == 'Horsepower'
    assert horsepower_field.db_column == f'field_{number_field.id}'
    assert horsepower_field.null

    assert isinstance(for_sale_field, models.BooleanField)
    assert for_sale_field.verbose_name == 'For sale'
    assert for_sale_field.db_column == f'field_{boolean_field.id}'
    assert not for_sale_field.default

    table_2 = data_fixture.create_database_table(name='House')
    data_fixture.create_number_field(table=table_2, order=0, name='Sale price',
                                     number_type='DECIMAL', number_decimal_places=3,
                                     number_negative=True)

    model = table_2.get_model(attribute_names=True)
    sale_price_field = model._meta.get_field('sale_price')
    assert isinstance(sale_price_field, models.DecimalField)
    assert sale_price_field.decimal_places == 3
    assert sale_price_field.null

    model_2 = table.get_model(fields=[number_field], field_ids=[text_field.id],
                              attribute_names=True)
    assert len(model_2._meta.get_fields()) == 3 + default_model_fields_count

    color_field = model_2._meta.get_field('color')
    assert color_field
    assert color_field.db_column == f'field_{text_field.id}'

    horsepower_field = model_2._meta.get_field('horsepower')
    assert horsepower_field
    assert horsepower_field.db_column == f'field_{number_field.id}'

    model_3 = table.get_model()
    assert model_3._meta.db_table == f'database_table_{table.id}'
    assert len(model_3._meta.get_fields()) == 4 + default_model_fields_count

    field_1 = model_3._meta.get_field(f'field_{text_field.id}')
    assert isinstance(field_1, models.TextField)
    assert field_1.db_column == f'field_{text_field.id}'

    field_2 = model_3._meta.get_field(f'field_{number_field.id}')
    assert isinstance(field_2, models.DecimalField)
    assert field_2.db_column == f'field_{number_field.id}'

    field_3 = model_3._meta.get_field(f'field_{boolean_field.id}')
    assert isinstance(field_3, models.BooleanField)
    assert field_3.db_column == f'field_{boolean_field.id}'

    text_field_2 = data_fixture.create_text_field(table=table, order=3, name='Color',
                                                  text_default='orange')
    model = table.get_model(attribute_names=True)
    field_names = [f.name for f in model._meta.get_fields()]
    assert len(field_names) == 5 + default_model_fields_count
    assert f'{text_field.model_attribute_name}_field_{text_field.id}' in field_names
    assert f'{text_field_2.model_attribute_name}_field_{text_field.id}' in field_names

    # Test if the fields are also returns if requested.
    model = table.get_model()
    fields = model._field_objects
    assert len(fields.items()) == 4

    assert fields[text_field.id]['field'].id == text_field.id
    assert fields[text_field.id]['type'].type == 'text'
    assert fields[text_field.id]['name'] == f'field_{text_field.id}'

    assert fields[number_field.id]['field'].id == number_field.id
    assert fields[number_field.id]['type'].type == 'number'
    assert fields[number_field.id]['name'] == f'field_{number_field.id}'

    assert fields[boolean_field.id]['field'].id == boolean_field.id
    assert fields[boolean_field.id]['type'].type == 'boolean'
    assert fields[boolean_field.id]['name'] == f'field_{boolean_field.id}'

    assert fields[text_field_2.id]['field'].id == text_field_2.id
    assert fields[text_field_2.id]['type'].type == 'text'
    assert fields[text_field_2.id]['name'] == f'field_{text_field_2.id}'


@pytest.mark.django_db
def test_enhance_by_fields_queryset(data_fixture):
    table = data_fixture.create_database_table(name='Cars')
    field = data_fixture.create_text_field(table=table, order=0, name='Color')

    model = table.get_model(attribute_names=True)
    mocked_type = MagicMock()

    model._field_objects[field.id]['type'] = mocked_type
    model.objects.all().enhance_by_fields()

    mocked_type.enhance_queryset.assert_called()


@pytest.mark.django_db
def test_search_all_fields_queryset(data_fixture):
    table = data_fixture.create_database_table(name='Cars')
    data_fixture.create_text_field(table=table, order=0, name='Name')
    data_fixture.create_text_field(table=table, order=1, name='Color')
    data_fixture.create_number_field(table=table, order=2, name='Price')
    data_fixture.create_long_text_field(table=table, order=3, name='Description')

    model = table.get_model(attribute_names=True)
    row_1 = model.objects.create(
        name='BMW',
        color='Blue',
        price=10000,
        description='This is the fastest car there is.'
    )
    row_2 = model.objects.create(
        name='Audi',
        color='Orange',
        price=20000,
        description='This is the most expensive car we have.'
    )
    row_3 = model.objects.create(
        name='Volkswagen',
        color='White',
        price=5000,
        description='The oldest car that we have.'
    )

    results = model.objects.all().search_all_fields('FASTEST')
    assert row_1 in results

    results = model.objects.all().search_all_fields('car')
    assert len(results) == 3
    assert row_1 in results
    assert row_2 in results
    assert row_3 in results

    results = model.objects.all().search_all_fields('oldest')
    assert len(results) == 1
    assert row_3 in results

    results = model.objects.all().search_all_fields('Audi')
    assert len(results) == 1
    assert row_2 in results

    results = model.objects.all().search_all_fields(row_1.id)
    assert len(results) == 1
    assert row_1 in results

    results = model.objects.all().search_all_fields(row_3.id)
    assert len(results) == 1
    assert row_3 in results

    results = model.objects.all().search_all_fields('white car')
    assert len(results) == 0


@pytest.mark.django_db
def test_order_by_fields_string_queryset(data_fixture):
    table = data_fixture.create_database_table(name='Cars')
    table_2 = data_fixture.create_database_table(database=table.database)
    name_field = data_fixture.create_text_field(table=table, order=0, name='Name')
    color_field = data_fixture.create_text_field(table=table, order=1, name='Color')
    price_field = data_fixture.create_number_field(table=table, order=2, name='Price')
    description_field = data_fixture.create_long_text_field(
        table=table, order=3, name='Description'
    )
    link_field = data_fixture.create_link_row_field(table=table,
                                                    link_row_table=table_2)

    model = table.get_model(attribute_names=True)
    row_1 = model.objects.create(
        name='BMW',
        color='Blue',
        price=10000,
        description='Sports car.'
    )
    row_2 = model.objects.create(
        name='Audi',
        color='Orange',
        price=20000,
        description='This is the most expensive car we have.'
    )
    row_3 = model.objects.create(
        name='Volkswagen',
        color='White',
        price=5000,
        description='A very old car.'
    )
    row_4 = model.objects.create(
        name='Volkswagen',
        color='Green',
        price=4000,
        description='Strange color.'
    )

    with pytest.raises(ValueError):
        model.objects.all().order_by_fields_string('xxxx')

    with pytest.raises(ValueError):
        model.objects.all().order_by_fields_string('')

    with pytest.raises(ValueError):
        model.objects.all().order_by_fields_string('id')

    with pytest.raises(OrderByFieldNotFound):
        model.objects.all().order_by_fields_string('field_99999')

    with pytest.raises(OrderByFieldNotPossible):
        model.objects.all().order_by_fields_string(f'field_{link_field.id}')

    results = model.objects.all().order_by_fields_string(
        f'-field_{price_field.id}'
    )
    assert results[0].id == row_2.id
    assert results[1].id == row_1.id
    assert results[2].id == row_3.id
    assert results[3].id == row_4.id

    results = model.objects.all().order_by_fields_string(
        f'field_{name_field.id},-field_{price_field.id}'
    )
    assert results[0].id == row_2.id
    assert results[1].id == row_1.id
    assert results[2].id == row_3.id
    assert results[3].id == row_4.id

    results = model.objects.all().order_by_fields_string(
        f'-field_{price_field.id}'
    )
    assert results[0].id == row_2.id
    assert results[1].id == row_1.id
    assert results[2].id == row_3.id
    assert results[3].id == row_4.id

    results = model.objects.all().order_by_fields_string(
        f'{description_field.id},-field_{color_field.id}'
    )
    assert results[0].id == row_3.id
    assert results[1].id == row_1.id
    assert results[2].id == row_4.id
    assert results[3].id == row_2.id

    row_5 = model.objects.create(
        name='Audi',
        color='Red',
        price=2000,
        description='Old times',
        order=Decimal('0.1')
    )

    row_2.order = Decimal('0.1')
    results = model.objects.all().order_by_fields_string(
        f'{name_field.id}'
    )
    assert results[0].id == row_5.id
    assert results[1].id == row_2.id
    assert results[2].id == row_1.id
    assert results[3].id == row_3.id
    assert results[4].id == row_4.id


@pytest.mark.django_db
def test_filter_by_fields_object_queryset(data_fixture):
    table = data_fixture.create_database_table(name='Cars')
    data_fixture.create_database_table(database=table.database)
    name_field = data_fixture.create_text_field(table=table, order=0, name='Name')
    data_fixture.create_text_field(table=table, order=1, name='Color')
    price_field = data_fixture.create_number_field(table=table, order=2, name='Price')
    description_field = data_fixture.create_long_text_field(
        table=table, order=3, name='Description'
    )

    model = table.get_model(attribute_names=True)
    row_1 = model.objects.create(
        name='BMW',
        color='Blue',
        price=10000,
        description='Sports car.'
    )
    row_2 = model.objects.create(
        name='Audi',
        color='Orange',
        price=20000,
        description='This is the most expensive car we have.'
    )
    model.objects.create(
        name='Volkswagen',
        color='White',
        price=5000,
        description='A very old car.'
    )
    row_4 = model.objects.create(
        name='Volkswagen',
        color='Green',
        price=4000,
        description=''
    )

    with pytest.raises(ValueError):
        model.objects.all().filter_by_fields_object(filter_object={
            f'filter__field_999999__equal': ['BMW'],
        }, filter_type='RANDOM')

    with pytest.raises(FilterFieldNotFound):
        model.objects.all().filter_by_fields_object(filter_object={
            f'filter__field_999999__equal': ['BMW'],
        }, filter_type='AND')

    with pytest.raises(ViewFilterTypeDoesNotExist):
        model.objects.all().filter_by_fields_object(filter_object={
            f'filter__field_{name_field.id}__INVALID': ['BMW'],
        }, filter_type='AND')

    with pytest.raises(ViewFilterTypeNotAllowedForField):
        model.objects.all().filter_by_fields_object(filter_object={
            f'filter__field_{price_field.id}__contains': '10',
        }, filter_type='AND')

    # All the entries are not following the correct format and should be ignored.
    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__not__equal': ['BMW'],
        f'filter__field_{price_field.id}_equal': '10000',
        f'filters__field_{price_field.id}__equal': '10000',
    }, filter_type='AND')
    assert len(results) == 4

    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__field_{name_field.id}__equal': ['BMW'],
        f'filter__field_{price_field.id}__equal': '10000',
    }, filter_type='AND')
    assert len(results) == 1
    assert results[0].id == row_1.id

    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__field_{name_field.id}__equal': ['BMW', 'Audi'],
    }, filter_type='AND')
    assert len(results) == 0

    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__field_{name_field.id}__equal': ['BMW', 'Audi'],
    }, filter_type='OR')
    assert len(results) == 2
    assert results[0].id == row_1.id
    assert results[1].id == row_2.id

    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__field_{price_field.id}__higher_than': '5500',
    }, filter_type='AND')
    assert len(results) == 2
    assert results[0].id == row_1.id
    assert results[1].id == row_2.id

    results = model.objects.all().filter_by_fields_object(filter_object={
        f'filter__field_{description_field.id}__empty': '',
    }, filter_type='AND')
    assert len(results) == 1
    assert results[0].id == row_4.id