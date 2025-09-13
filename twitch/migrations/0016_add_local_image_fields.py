from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add local image FileFields to models for caching Twitch images."""

    dependencies = [
        ("twitch", "0015_alter_dropbenefitedge_benefit_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="box_art_file",
            field=models.FileField(blank=True, null=True, upload_to="games/box_art/"),
        ),
        migrations.AddField(
            model_name="dropcampaign",
            name="image_file",
            field=models.FileField(blank=True, null=True, upload_to="campaigns/images/"),
        ),
        migrations.AddField(
            model_name="dropbenefit",
            name="image_file",
            field=models.FileField(blank=True, null=True, upload_to="benefits/images/"),
        ),
    ]
