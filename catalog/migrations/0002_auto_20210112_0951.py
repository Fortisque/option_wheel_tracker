# Generated by Django 3.1.4 on 2021-01-12 17:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='optionwheel',
            name='is_active',
            field=models.BooleanField(db_index=True),
        ),
        migrations.AlterField(
            model_name='stockticker',
            name='name',
            field=models.CharField(db_index=True, help_text='Enter a ticker, like TSLA.', max_length=200, unique=True),
        ),
    ]
