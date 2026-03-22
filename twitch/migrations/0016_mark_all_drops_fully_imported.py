from django.db import migrations


def mark_all_drops_fully_imported(apps, schema_editor) -> None:  # noqa: ANN001
    """Marks all existing DropCampaigns as fully imported.

    This was needed to ensure that the Twitch API view only returns campaigns that are ready for display.
    """
    DropCampaign = apps.get_model("twitch", "DropCampaign")
    DropCampaign.objects.all().update(is_fully_imported=True)


class Migration(migrations.Migration):
    """Marks all existing DropCampaigns as fully imported.

    This was needed to ensure that the Twitch API view only returns campaigns that are ready for display.
    """

    dependencies = [
        ("twitch", "0015_dropcampaign_is_fully_imported"),
    ]

    operations = [
        migrations.RunPython(mark_all_drops_fully_imported),
    ]
