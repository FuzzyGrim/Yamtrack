from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0051_user_home_separate_incoming"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="home_separate_incoming",
        ),
    ]
