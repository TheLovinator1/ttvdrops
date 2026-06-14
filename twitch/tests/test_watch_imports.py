from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from django.core.management.base import CommandError

from twitch.management.commands.watch_imports import Command

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureResult


class TestWatchImportsCommand:
    """Tests for the watch_imports management command."""

    def test_get_watch_path_returns_resolved_directory(self, tmp_path: Path) -> None:
        """It should return the resolved path when a valid directory is provided."""
        command = Command()

        result: Path = command.get_watch_path({"path": str(tmp_path)})

        assert result == tmp_path.resolve()

    def test_get_watch_path_raises_for_missing_path(self, tmp_path: Path) -> None:
        """It should raise CommandError when the path does not exist."""
        command = Command()
        missing_path: Path = tmp_path / "missing"

        with pytest.raises(CommandError, match="Path does not exist"):
            command.get_watch_path({"path": str(missing_path)})

    def test_get_watch_path_raises_for_file_path(self, tmp_path: Path) -> None:
        """It should raise CommandError when the path is not a directory."""
        command = Command()
        file_path: Path = tmp_path / "data.json"
        file_path.write_text("{}", encoding="utf-8")

        with pytest.raises(CommandError, match="Path is not a directory"):
            command.get_watch_path({"path": str(file_path)})

    def test_import_json_files_imports_only_json_files(self, tmp_path: Path) -> None:
        """It should call importer for .json files and ignore other entries."""
        command = Command()
        importer_command = MagicMock()

        json_file_1: Path = tmp_path / "one.json"
        json_file_2: Path = tmp_path / "two.json"
        ignored_txt: Path = tmp_path / "notes.txt"
        ignored_dir: Path = tmp_path / "nested.json"

        json_file_1.write_text("{}", encoding="utf-8")
        json_file_2.write_text("[]", encoding="utf-8")
        ignored_txt.write_text("ignored", encoding="utf-8")
        ignored_dir.mkdir()

        command.import_json_files(
            importer_command=importer_command,
            watch_path=tmp_path,
        )

        imported_paths: list[Path] = [
            call.kwargs["path"] for call in importer_command.handle.call_args_list
        ]
        assert set(imported_paths) == {json_file_1, json_file_2}

    def test_import_json_files_no_json_files_does_nothing(self, tmp_path: Path) -> None:
        """It should not call importer when no JSON files are present."""
        command = Command()
        importer_command = MagicMock()

        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

        command.import_json_files(
            importer_command=importer_command,
            watch_path=tmp_path,
        )

        importer_command.handle.assert_not_called()

    def test_handle_stops_cleanly_on_keyboard_interrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """It should print a warning and stop when interrupted by keyboard."""
        command = Command()
        importer_instance = MagicMock()

        monkeypatch.setattr(command, "get_watch_path", lambda options: tmp_path)
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.BetterImportDropsCommand",
            lambda: importer_instance,
        )
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.sleep",
            lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

        command.handle(path=str(tmp_path))

        captured: CaptureResult[str] = capsys.readouterr()
        assert "Watching" in captured.out
        assert "Received keyboard interrupt. Stopping watch..." in captured.out

    def test_handle_reports_command_errors_and_continues_until_interrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """It should report CommandError from import and continue the loop."""
        command = Command()
        importer_instance = MagicMock()
        sleep_calls: dict[str, int] = {"count": 0}

        def fake_sleep(_seconds: int) -> None:
            """Simulate sleep and raise KeyboardInterrupt after 2 calls.

            Args:
                _seconds: The number of seconds to sleep (ignored).

            Raises:
                KeyboardInterrupt: After being called twice to simulate user interrupt.
            """
            sleep_calls["count"] += 1
            if sleep_calls["count"] == 2:
                raise KeyboardInterrupt

        def fake_import_json_files(
            *,
            importer_command: MagicMock,
            watch_path: Path,
        ) -> None:
            """Simulate an import that raises CommandError.

            Args:
                importer_command: The mock importer command instance (ignored).
                watch_path: The path being watched (ignored).

            Raises:
                CommandError: Always raised to simulate an import error.
            """
            msg = "bad import"
            raise CommandError(msg)

        monkeypatch.setattr(
            command,
            "get_watch_path",
            lambda options: tmp_path,
        )
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.BetterImportDropsCommand",
            lambda: importer_instance,
        )
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.sleep",
            fake_sleep,
        )
        monkeypatch.setattr(
            command,
            "import_json_files",
            fake_import_json_files,
        )

        command.handle(path=str(tmp_path))

        captured: CaptureResult[str] = capsys.readouterr()
        assert "Import command error: bad import" in captured.out
        assert "Received keyboard interrupt. Stopping watch..." in captured.out

    def test_handle_wraps_unexpected_errors_in_command_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """It should wrap unexpected exceptions in a CommandError."""
        command = Command()
        importer_instance = MagicMock()

        monkeypatch.setattr(
            command,
            "get_watch_path",
            lambda options: tmp_path,
        )
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.BetterImportDropsCommand",
            lambda: importer_instance,
        )
        monkeypatch.setattr(
            "twitch.management.commands.watch_imports.sleep",
            lambda _seconds: None,
        )

        def fake_import_json_files(
            *,
            importer_command: MagicMock,
            watch_path: Path,
        ) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            command,
            "import_json_files",
            fake_import_json_files,
        )

        with pytest.raises(CommandError, match="Error while watching directory: boom"):
            command.handle(path=str(tmp_path))
