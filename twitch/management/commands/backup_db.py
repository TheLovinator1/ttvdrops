import io
import json
import os
import shutil
import subprocess  # noqa: S404
from compression import zstd
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import connection as django_connection
from django.utils import timezone

if TYPE_CHECKING:
    import sqlite3
    from argparse import ArgumentParser


class SupportsStr(Protocol):
    """Protocol for values that provide a string representation."""

    def __str__(self) -> str: ...


type SqlSerializable = bool | int | float | bytes | SupportsStr | None


class Command(BaseCommand):
    """Create a compressed SQL dump of the Twitch and Kick dataset tables."""

    help = "Create a compressed SQL dump of the Twitch and Kick dataset tables."

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
        """Run the backup command and write a zstd SQL dump.

        Args:
            **options: Command-line options for output directory and filename prefix.

        Raises:
            CommandError: When the database connection fails or pg_dump is not available.
        """
        output_dir: Path = Path(options["output_dir"]).expanduser()
        prefix: str = str(options["prefix"]).strip() or "ttvdrops"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use Django's database connection to ensure we connect to the test DB during tests
        django_connection.ensure_connection()
        connection = django_connection.connection
        if connection is None:
            msg = "Database connection could not be established."
            raise CommandError(msg)

        timestamp: str = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
        output_path: Path = output_dir / f"{prefix}-{timestamp}.sql.zst"

        allowed_tables = sorted({
            *_get_allowed_tables("twitch_"),
            *_get_allowed_tables("kick_"),
        })
        if not allowed_tables:
            self.stdout.write(
                self.style.WARNING("No twitch or kick tables found to back up."),
            )
            return

        if django_connection.vendor == "postgresql":
            _write_postgres_dump(output_path, allowed_tables)
        elif django_connection.vendor == "sqlite":
            with (
                output_path.open("wb") as raw_handle,
                zstd.open(raw_handle, "w") as compressed,
                io.TextIOWrapper(compressed, encoding="utf-8") as handle,
            ):
                _write_sqlite_dump(handle, connection, allowed_tables)
        else:
            msg = f"Unsupported database backend: {django_connection.vendor}"
            raise CommandError(msg)

        json_path: Path = output_dir / f"{prefix}-{timestamp}.json.zst"
        _write_json_dump(json_path, allowed_tables)

        created_at: datetime = datetime.fromtimestamp(
            output_path.stat().st_mtime,
            tz=timezone.get_current_timezone(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup created: {output_path} (updated {created_at.isoformat()})",
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"JSON backup created: {json_path}"))
        self.stdout.write(self.style.SUCCESS(f"Included tables: {len(allowed_tables)}"))


def _get_allowed_tables(prefix: str) -> list[str]:
    """Fetch table names that match the allowed prefix.

    Args:
        prefix: Table name prefix to include.

    Returns:
        List of table names.
    """
    with django_connection.cursor() as cursor:
        if django_connection.vendor == "postgresql":
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE %s ORDER BY tablename",
                [f"{prefix}%"],
            )
        else:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
                (f"{prefix}%",),
            )
        return [row[0] for row in cursor.fetchall()]


def _write_sqlite_dump(
    handle: io.TextIOBase,
    connection: sqlite3.Connection,
    tables: list[str],
) -> None:
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


def _write_table_rows(
    handle: io.TextIOBase,
    connection: sqlite3.Connection,
    table: str,
) -> None:
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


def _write_indexes(
    handle: io.TextIOBase,
    connection: sqlite3.Connection,
    tables: list[str],
) -> None:
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


def _write_postgres_dump(output_path: Path, tables: list[str]) -> None:
    """Write a PostgreSQL dump using pg_dump into a zstd-compressed file.

    Args:
        output_path: Destination path for the zstd file.
        tables: Table names to include.

    Raises:
        CommandError: When pg_dump fails or is not found.
    """
    pg_dump_path = shutil.which("pg_dump")
    if not pg_dump_path:
        msg = "pg_dump was not found. Install PostgreSQL client tools and retry."
        raise CommandError(msg)

    settings_dict = django_connection.settings_dict
    env = os.environ.copy()
    password = settings_dict.get("PASSWORD")
    if password:
        env["PGPASSWORD"] = str(password)

    cmd = [
        pg_dump_path,
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "--column-inserts",
        "--quote-all-identifiers",
        "--encoding=UTF8",
        "--dbname",
        str(settings_dict.get("NAME", "")),
    ]

    host = settings_dict.get("HOST")
    port = settings_dict.get("PORT")
    user = settings_dict.get("USER")

    if host:
        cmd.extend(["--host", str(host)])
    if port:
        cmd.extend(["--port", str(port)])
    if user:
        cmd.extend(["--username", str(user)])

    for table in tables:
        cmd.extend(["-t", f"public.{table}"])

    process = subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        msg = "Failed to start pg_dump process."
        raise CommandError(msg)

    if process.stdout is None or process.stderr is None:
        process.kill()
        msg = "pg_dump process did not provide stdout or stderr."
        raise CommandError(msg)

    with output_path.open("wb") as raw_handle, zstd.open(raw_handle, "w") as compressed:
        for chunk in iter(lambda: process.stdout.read(64 * 1024), b""):  # pyright: ignore[reportOptionalMemberAccess]
            compressed.write(chunk)

    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
    return_code = process.wait()
    if return_code != 0:
        msg = f"pg_dump failed with exit code {return_code}: {stderr_output.strip()}"
        raise CommandError(msg)


def _sql_literal(value: SqlSerializable) -> str:
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


def _json_default(value: bytes | SupportsStr) -> str:
    """Convert non-serializable values to JSON-compatible strings.

    Args:
        value: Value to convert.

    Returns:
        String representation.
    """
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _write_json_dump(output_path: Path, tables: list[str]) -> None:
    """Write a JSON dump of all tables into a zstd-compressed file.

    Args:
        output_path: Destination path for the zstd file.
        tables: Table names to include.
    """
    data: dict[str, list[dict]] = {}
    with django_connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608
            columns: list[str] = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            data[table] = [dict(zip(columns, row, strict=False)) for row in rows]

    with (
        output_path.open("wb") as raw_handle,
        zstd.open(raw_handle, "w") as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as handle,
    ):
        json.dump(data, handle, default=_json_default)
