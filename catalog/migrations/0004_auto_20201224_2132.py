# Generated by Django 3.1.4 on 2020-12-24 21:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_auto_20201219_0817'),
    ]

    operations = [
        migrations.AlterField(
            model_name='optionwheel',
            name='total_days_active',
            field=models.IntegerField(default=None, null=True),
        ),
        migrations.AlterField(
            model_name='stockticker',
            name='recommendation',
            field=models.CharField(choices=[('NO', 'None'), ('ST', 'Stable Choice'), ('HV', 'High Volatility')], default='NO', max_length=2),
        ),
    ]
