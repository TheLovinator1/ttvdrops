from __future__ import annotations

import io
from compression import zstd
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection as django_connection
from django.utils import timezone

if TYPE_CHECKING:
    import sqlite3
    from argparse import ArgumentParser


class Command(BaseCommand):
    """Create a compressed SQL dump of the Twitch dataset tables."""

    help = "Create a compressed SQL dump of the Twitch dataset tables."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Define arguments for the backup command."""
        parser.add_argument(
            "--output-dir",
            default=str(settings.DATA_DIR / "datasets"),
            help="Directory where the backup will be written.",
        )
        parser.add_argument(
            "--prefix",
            default="ttvdrops",
            help="Filename prefix for the backup file.",
        )

    def handle(self, **options: str) -> None:
        """Run the backup command and write a zstd SQL dump."""
        output_dir: Path = Path(options["output_dir"]).expanduser()
        prefix: str = str(options["prefix"]).strip() or "ttvdrops"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use Django's database connection to ensure we connect to the test DB during tests
        connection = django_connection.connection
        if connection is None:
            # Force connection if not already established
            django_connection.ensure_connection()
            connection = django_connection.connection

        timestamp: str = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
        output_path: Path = output_dir / f"{prefix}-{timestamp}.sql.zst"

        allowed_tables = _get_allowed_tables(connection, "twitch_")
        if not allowed_tables:
            self.stdout.write(self.style.WARNING("No twitch tables found to back up."))
            return

        with (
            output_path.open("wb") as raw_handle,
            zstd.open(raw_handle, "w") as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8") as handle,
        ):
            _write_dump(handle, connection, allowed_tables)

        created_at: datetime = datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.get_current_timezone())
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup created: {output_path} (updated {created_at.isoformat()})",
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"Included tables: {len(allowed_tables)}"))


def _get_allowed_tables(connection: sqlite3.Connection, prefix: str) -> list[str]:
    """Fetch table names that match the allowed prefix.

    Args:
        connection: SQLite connection.
        prefix: Table name prefix to include.

    Returns:
        List of table names.
    """
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
        (f"{prefix}%",),
    )
    return [row[0] for row in cursor.fetchall()]


def _write_dump(handle: io.TextIOBase, connection: sqlite3.Connection, tables: list[str]) -> None:
    """Write a SQL dump containing schema and data for the requested tables.

    Args:
        handle: Text handle for output.
        connection: SQLite connection.
        tables: Table names to include.
    """
    handle.write("PRAGMA foreign_keys=OFF;\n")
    handle.write("BEGIN TRANSACTION;\n")

    for table in tables:
        create_sql = _get_table_schema(connection, table)
        if not create_sql:
            continue
        handle.write(f'DROP TABLE IF EXISTS "{table}";\n')
        handle.write(f"{create_sql};\n")
        _write_table_rows(handle, connection, table)

    _write_indexes(handle, connection, tables)

    handle.write("COMMIT;\n")
    handle.write("PRAGMA foreign_keys=ON;\n")


def _get_table_schema(connection: sqlite3.Connection, table: str) -> str:
    """Fetch the CREATE TABLE statement for a table.

    Args:
        connection: SQLite connection.
        table: Table name.

    Returns:
        The SQL string or an empty string when unavailable.
    """
    cursor = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else ""


def _write_table_rows(handle: io.TextIOBase, connection: sqlite3.Connection, table: str) -> None:
    """Write INSERT statements for a table.

    Args:
        handle: Text handle for output.
        connection: SQLite connection.
        table: Table name.
    """
    cursor = connection.execute(f'SELECT * FROM "{table}"')  # noqa: S608
    columns = [description[0] for description in cursor.description]
    for row in cursor.fetchall():
        values = ", ".join(_sql_literal(row[idx]) for idx in range(len(columns)))
        handle.write(f'INSERT INTO "{table}" VALUES ({values});\n')  # noqa: S608


def _write_indexes(handle: io.TextIOBase, connection: sqlite3.Connection, tables: list[str]) -> None:
    """Write CREATE INDEX statements for included tables.

    Args:
        handle: Text handle for output.
        connection: SQLite connection.
        tables: Table names to include.
    """
    table_set = set(tables)
    cursor = connection.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name",
    )
    for tbl_name, sql in cursor.fetchall():
        if tbl_name in table_set and sql:
            handle.write(f"{sql};\n")


def _sql_literal(value: object) -> str:
    """Convert a Python value to a SQL literal.

    Args:
        value: Value to convert.

    Returns:
        SQL literal string.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"
