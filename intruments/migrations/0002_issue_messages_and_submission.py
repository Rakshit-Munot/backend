from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("intruments", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="issuerequest",
            name="submission_status",
            field=models.CharField(choices=[("not_required", "Not Required"), ("pending", "Pending"), ("submitted", "Submitted")], default="pending", max_length=20),
        ),
        migrations.AddField(
            model_name="issuerequest",
            name="submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="IssueMessage",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("msg_type", models.CharField(choices=[("admin", "Admin"), ("system", "System")], default="admin", max_length=10)),
                ("text", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("creator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("issue_request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="intruments.issuerequest")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
