# Generated by Django 5.0 on 2024-11-28 00:36

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("survey", "0002_alter_survey_owner_user_ids_alter_survey_status_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="useranswer",
            name="question",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="answers",
                to="survey.question",
            ),
        ),
        migrations.AlterField(
            model_name="useranswer",
            name="user_response",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="survey.userresponse"
            ),
        ),
        migrations.AlterField(
            model_name="userresponse",
            name="survey",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="user_responses",
                to="survey.survey",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="userresponse",
            unique_together={("survey", "user")},
        ),
        migrations.AddIndex(
            model_name="userresponse",
            index=models.Index(fields=["survey", "user"], name="survey_user_survey__678128_idx"),
        ),
    ]
