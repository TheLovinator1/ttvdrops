import csv
import io
import json
import math
import os
import shutil
from compression import zstd
from datetime import datetime as dt
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from twitch.management.commands.backup_db import _get_allowed_tables
from twitch.management.commands.backup_db import _json_default
from twitch.management.commands.backup_db import _sql_literal
from twitch.management.commands.backup_db import _write_csv_dump
from twitch.management.commands.backup_db import _write_json_dump
from twitch.management.commands.backup_db import _write_postgres_dump
from twitch.management.commands.backup_db import _write_sqlite_dump
from twitch.models import Game
from twitch.models import Organization

if TYPE_CHECKING:
    from csv import Reader
    from datetime import datetime
    from pathlib import Path

    from django.test import Client
    from django.test.client import _MonkeyPatchedWSGIResponse


def _skip_if_pg_dump_missing() -> None:
    if connection.vendor == "postgresql" and not shutil.which("pg_dump"):
        pytest.skip("pg_dump is not available")


@pytest.mark.django_db
class TestBackupCommand:
    """Tests for the backup_db management command."""

    def test_backup_creates_file(self, tmp_path: Path) -> None:
        """Test that backup command creates a zstd compressed file."""
        _skip_if_pg_dump_missing()
        # Create test data so tables exist
        Organization.objects.create(twitch_id="test000", name="Test Org")

        output_dir = tmp_path / "backups"
        output_dir.mkdir()

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        backup_files = list(output_dir.glob("test-*.sql.zst"))
        assert len(backup_files) == 1
        assert backup_files[0].exists()
        assert backup_files[0].stat().st_size > 0

    def test_backup_contains_sql_content(self, tmp_path: Path) -> None:
        """Test that backup file contains valid SQL content."""
        _skip_if_pg_dump_missing()
        output_dir = tmp_path / "backups"
        output_dir.mkdir()

        # Create some test data
        org = Organization.objects.create(twitch_id="test123", name="Test Org")
        game = Game.objects.create(twitch_id="game456", display_name="Test Game")
        game.owners.add(org)

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        backup_file = next(iter(output_dir.glob("test-*.sql.zst")))

        # Decompress and read content
        with (
            backup_file.open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            content = handle.read()

        if connection.vendor == "postgresql":
            assert "CREATE TABLE" in content
            assert "INSERT INTO" in content
        else:
            assert "PRAGMA foreign_keys=OFF;" in content
            assert "BEGIN TRANSACTION;" in content
            assert "COMMIT;" in content
        assert "twitch_organization" in content
        assert "twitch_game" in content
        assert "Test Org" in content

    def test_backup_excludes_non_app_tables(self, tmp_path: Path) -> None:
        """Test that backup includes app tables and excludes non-app tables."""
        _skip_if_pg_dump_missing()
        # Create test data so tables exist
        Organization.objects.create(twitch_id="test001", name="Test Org")

        output_dir: Path = tmp_path / "backups"
        output_dir.mkdir()

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        backup_file: Path = next(iter(output_dir.glob("test-*.sql.zst")))

        with (
            backup_file.open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            content: str = handle.read()

        # Should NOT contain django admin, silk, or debug toolbar tables
        assert "django_session" not in content
        assert "django_migrations" not in content
        assert "django_content_type" not in content
        assert "silk_" not in content
        assert "debug_toolbar_" not in content
        assert "django_admin_log" not in content
        assert "auth_" not in content
        assert "youtube_" not in content

        # Should contain twitch and kick tables
        assert "twitch_" in content
        assert "kick_" in content

    def test_backup_with_custom_prefix(self, tmp_path: Path) -> None:
        """Test that custom prefix is used in filename."""
        _skip_if_pg_dump_missing()
        # Create test data so tables exist
        Organization.objects.create(twitch_id="test002", name="Test Org")

        output_dir = tmp_path / "backups"
        output_dir.mkdir()

        call_command("backup_db", output_dir=str(output_dir), prefix="custom")

        backup_files = list(output_dir.glob("custom-*.sql.zst"))
        assert len(backup_files) == 1

    def test_backup_creates_output_directory(self, tmp_path: Path) -> None:
        """Test that backup command creates output directory if missing."""
        _skip_if_pg_dump_missing()
        # Create test data so tables exist
        Organization.objects.create(twitch_id="test003", name="Test Org")

        output_dir = tmp_path / "nonexistent" / "backups"

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        assert output_dir.exists()
        assert len(list(output_dir.glob("test-*.sql.zst"))) == 1

    def test_backup_uses_default_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that backup uses DATA_DIR/datasets by default."""
        _skip_if_pg_dump_missing()
        # Create test data so tables exist
        Organization.objects.create(twitch_id="test004", name="Test Org")

        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir(exist_ok=True, parents=True)

        call_command("backup_db")

        backup_files = list(datasets_dir.glob("ttvdrops-*.sql.zst"))
        assert len(backup_files) >= 1

    def test_backup_creates_json_file(self, tmp_path: Path) -> None:
        """Test that backup command creates a JSON file alongside the SQL dump."""
        _skip_if_pg_dump_missing()
        Organization.objects.create(twitch_id="test_json", name="Test Org JSON")

        output_dir: Path = tmp_path / "backups"
        output_dir.mkdir()

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        json_files: list[Path] = list(output_dir.glob("test-*.json.zst"))
        assert len(json_files) == 1

        with (
            json_files[0].open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            data = json.load(handle)

        assert isinstance(data, dict)
        assert "twitch_organization" in data
        assert any(
            row.get("name") == "Test Org JSON" for row in data["twitch_organization"]
        )

    def test_backup_creates_single_csv_file(self, tmp_path: Path) -> None:
        """Test that backup command creates a single CSV file alongside the SQL dump."""
        _skip_if_pg_dump_missing()
        Organization.objects.create(twitch_id="test_csv", name="Test Org CSV")

        output_dir: Path = tmp_path / "backups"
        output_dir.mkdir()

        call_command("backup_db", output_dir=str(output_dir), prefix="test")

        csv_files: list[Path] = list(output_dir.glob("test-*.csv.zst"))
        assert len(csv_files) == 1

        with (
            csv_files[0].open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            reader: Reader = csv.reader(handle)
            rows: list[list[str]] = list(reader)

        assert len(rows) >= 2  # header + at least one data row
        assert rows[0] == ["table", "row_json"]
        data_rows: list[list[str]] = [
            row for row in rows[1:] if row and row[0] == "twitch_organization"
        ]
        assert any("Test Org CSV" in row[1] for row in data_rows)


@pytest.mark.django_db
class TestBackupHelperFunctions:
    """Tests for backup command helper functions."""

    def test_get_allowed_tables_filters_by_prefix(self) -> None:
        """Test that _get_allowed_tables returns only matching tables."""
        # Use Django's connection to access the test database
        tables = _get_allowed_tables("twitch_")

        assert len(tables) > 0
        assert all(table.startswith("twitch_") for table in tables)
        assert "twitch_organization" in tables
        assert "twitch_game" in tables

    def test_get_allowed_tables_excludes_non_matching(self) -> None:
        """Test that _get_allowed_tables excludes non-matching tables."""
        # Use Django's connection to access the test database
        tables = _get_allowed_tables("twitch_")

        # Should not include django, silk, or debug toolbar tables
        assert not any(table.startswith("django_") for table in tables)
        assert not any(table.startswith("silk_") for table in tables)
        assert not any(table.startswith("debug_toolbar_") for table in tables)

    def test_sql_literal_handles_none(self) -> None:
        """Test _sql_literal converts None to NULL."""
        assert _sql_literal(None) == "NULL"

    def test_sql_literal_handles_booleans(self) -> None:
        """Test _sql_literal converts booleans to 1/0."""
        assert _sql_literal(True) == "1"
        assert _sql_literal(False) == "0"

    def test_sql_literal_handles_numbers(self) -> None:
        """Test _sql_literal handles int and float."""
        assert _sql_literal(42) == "42"
        assert _sql_literal(math.pi) == str(math.pi)

    def test_sql_literal_handles_strings(self) -> None:
        """Test _sql_literal quotes and escapes strings."""
        assert _sql_literal("test") == "'test'"
        assert _sql_literal("o'reilly") == "'o''reilly'"
        assert _sql_literal("test\nline") == "'test\nline'"

    def test_sql_literal_handles_bytes(self) -> None:
        """Test _sql_literal converts bytes to hex notation."""
        assert _sql_literal(b"\x00\x01\x02") == "X'000102'"
        assert _sql_literal(b"hello") == "X'68656c6c6f'"

    def test_write_dump_includes_schema_and_data(self, tmp_path: Path) -> None:
        """Test _write_dump writes complete SQL dump."""
        # Create test data
        Organization.objects.create(twitch_id="test789", name="Write Test Org")

        tables = _get_allowed_tables("twitch_")

        if connection.vendor == "postgresql":
            if not shutil.which("pg_dump"):
                pytest.skip("pg_dump is not available")
            output_path = tmp_path / "backup.sql.zst"
            _write_postgres_dump(output_path, tables)
            with (
                output_path.open("rb") as raw_handle,
                zstd.open(raw_handle, "r") as compressed,
                io.TextIOWrapper(compressed, encoding="utf-8") as handle,
            ):
                content = handle.read()
            assert "CREATE TABLE" in content
            assert "INSERT INTO" in content
            assert "twitch_organization" in content
            assert "Write Test Org" in content
        else:
            db_connection = connection.connection
            output = io.StringIO()
            _write_sqlite_dump(output, db_connection, tables)
            content = output.getvalue()
            assert "PRAGMA foreign_keys=OFF;" in content
            assert "BEGIN TRANSACTION;" in content
            assert "COMMIT;" in content
            assert "PRAGMA foreign_keys=ON;" in content
            assert "CREATE TABLE" in content
            assert "twitch_organization" in content
            assert "INSERT INTO" in content
            assert "Write Test Org" in content

    def test_write_json_dump_creates_valid_json(self, tmp_path: Path) -> None:
        """Test _write_json_dump creates valid compressed JSON with all tables."""
        Organization.objects.create(
            twitch_id="test_json_helper",
            name="JSON Helper Org",
        )

        tables: list[str] = _get_allowed_tables("twitch_")
        output_path: Path = tmp_path / "backup.json.zst"
        _write_json_dump(output_path, tables)

        with (
            output_path.open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            data = json.load(handle)

        assert isinstance(data, dict)
        assert "twitch_organization" in data
        assert all(table in data for table in tables)
        assert any(
            row.get("name") == "JSON Helper Org" for row in data["twitch_organization"]
        )

    def test_write_csv_dump_creates_single_file(self, tmp_path: Path) -> None:
        """Test _write_csv_dump creates one combined compressed CSV file."""
        Organization.objects.create(twitch_id="test_csv_helper", name="CSV Helper Org")

        tables: list[str] = _get_allowed_tables("twitch_")
        path: Path = _write_csv_dump(
            tmp_path,
            "test",
            "20260317-120000",
            tables,
        )

        assert path.exists()
        assert path.name == "test-20260317-120000.csv.zst"

        with (
            path.open("rb") as raw_handle,
            zstd.open(raw_handle, "r") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            reader: Reader = csv.reader(handle)
            rows: list[list[str]] = list(reader)

        assert len(rows) >= 2  # header + at least one data row
        assert rows[0] == ["table", "row_json"]
        data_rows: list[list[str]] = [
            row for row in rows[1:] if row and row[0] == "twitch_organization"
        ]
        assert any("CSV Helper Org" in row[1] for row in data_rows)

    def test_json_default_handles_bytes(self) -> None:
        """Test _json_default converts bytes to hex string."""
        assert _json_default(b"\x00\x01") == "0001"
        assert _json_default(b"hello") == "68656c6c6f"

    def test_json_default_handles_other_types(self) -> None:
        """Test _json_default falls back to str() for other types."""
        value: datetime = dt(2026, 3, 17, 12, 0, 0, tzinfo=dt.now().astimezone().tzinfo)
        assert _json_default(value) == str(value)


@pytest.mark.django_db
class TestDatasetBackupViews:
    """Tests for dataset backup list and download views."""

    @pytest.fixture
    def datasets_dir(self, tmp_path: Path) -> Path:
        """Create a temporary datasets directory.

        Returns:
            Path to the created datasets directory.
        """
        datasets_dir: Path = tmp_path / "datasets"
        datasets_dir.mkdir()
        return datasets_dir

    @pytest.fixture
    def sample_backup(self, datasets_dir: Path) -> Path:
        """Create a sample backup file.

        Returns:
            Path to the created backup file.
        """
        backup_file: Path = datasets_dir / "ttvdrops-20260210-120000.sql.zst"
        with (
            backup_file.open("wb") as raw_handle,
            zstd.open(raw_handle, "w") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            handle.write("-- Sample backup content\n")
        return backup_file

    def test_dataset_list_view_shows_backups(
        self,
        client: Client,
        datasets_dir: Path,
        sample_backup: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that dataset list view displays backup files."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )

        assert response.status_code == 200
        assert b"ttvdrops-20260210-120000.sql.zst" in response.content
        assert b"1 datasets" in response.content or b"1 dataset" in response.content

    def test_dataset_list_view_empty_directory(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test dataset list view with empty directory."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )

        assert response.status_code == 200
        assert b"No dataset backups found" in response.content

    def test_dataset_list_view_sorts_by_date(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that backups are sorted by modification time."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        # Create multiple backup files with different timestamps
        older_backup: Path = datasets_dir / "ttvdrops-20260210-100000.sql.zst"
        newer_backup: Path = datasets_dir / "ttvdrops-20260210-140000.sql.zst"

        for backup in [older_backup, newer_backup]:
            with (
                backup.open("wb") as raw_handle,
                zstd.open(raw_handle, "w") as compressed,
                io.TextIOWrapper(compressed, encoding="utf-8") as handle,
            ):
                handle.write("-- Test\n")

        # Set explicit modification times to ensure proper sorting
        older_time = 1707561600  # 2024-02-10 10:00:00 UTC
        newer_time = 1707575400  # 2024-02-10 14:00:00 UTC
        os.utime(older_backup, (older_time, older_time))
        os.utime(newer_backup, (newer_time, newer_time))

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )

        content: str = response.content.decode()
        newer_pos: int = content.find("20260210-140000")
        older_pos: int = content.find("20260210-100000")

        # Newer backup should appear first (sorted descending)
        assert 0 < newer_pos < older_pos

    def test_dataset_download_view_success(
        self,
        client: Client,
        datasets_dir: Path,
        sample_backup: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test successful backup download."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse(
                "core:dataset_backup_download",
                args=["ttvdrops-20260210-120000.sql.zst"],
            ),
        )

        assert response.status_code == 200
        # FileResponse may use application/x-compressed for .zst files
        assert "attachment" in response["Content-Disposition"]
        assert "ttvdrops-20260210-120000.sql.zst" in response["Content-Disposition"]

    def test_dataset_download_prevents_path_traversal(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that path traversal attempts are blocked."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        # Attempt path traversal
        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backup_download", args=["../../../etc/passwd"]),
        )
        assert response.status_code == 404

    def test_dataset_download_rejects_invalid_extensions(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that files with invalid extensions cannot be downloaded."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        # Create a file with invalid extension
        invalid_file: Path = datasets_dir / "malicious.exe"
        invalid_file.write_text("not a backup", encoding="utf-8")

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backup_download", args=["malicious.exe"]),
        )
        assert response.status_code == 404

    def test_dataset_download_file_not_found(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test download returns 404 for non-existent file."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backup_download", args=["nonexistent.sql.zst"]),
        )
        assert response.status_code == 404

    def test_dataset_list_view_shows_file_sizes(
        self,
        client: Client,
        datasets_dir: Path,
        sample_backup: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that file sizes are displayed in human-readable format."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )

        assert response.status_code == 200
        # Should contain size information (bytes, KB, MB, or GB)
        content: str = response.content.decode()
        assert any(unit in content for unit in ["bytes", "KB", "MB", "GB"])

    def test_dataset_list_ignores_non_zst_files(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that non-zst files are ignored in listing."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        # Create various file types
        (datasets_dir / "backup.sql.zst").write_bytes(b"valid")
        (datasets_dir / "readme.txt").write_text("should be ignored")
        (datasets_dir / "old_backup.gz").write_bytes(b"should be ignored")

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backups"),
        )

        content: str = response.content.decode()
        assert "backup.sql.zst" in content
        assert "readme.txt" not in content
        assert "old_backup.gz" not in content

    def test_dataset_download_view_handles_subdirectories(
        self,
        client: Client,
        datasets_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test download works with files in subdirectories."""
        monkeypatch.setattr(settings, "DATA_DIR", datasets_dir.parent)

        # Create subdirectory with backup
        subdir: Path = datasets_dir / "2026" / "02"
        subdir.mkdir(parents=True)
        backup_file: Path = subdir / "backup.sql.zst"
        with (
            backup_file.open("wb") as raw_handle,
            zstd.open(raw_handle, "w") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            handle.write("-- Test\n")

        response: _MonkeyPatchedWSGIResponse = client.get(
            reverse("core:dataset_backup_download", args=["2026/02/backup.sql.zst"]),
        )

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
