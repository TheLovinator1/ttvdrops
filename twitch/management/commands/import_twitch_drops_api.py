"""Import drop and reward campaign data from the SunkwiBOT/twitch-drops-api repo.

This command fetches ``drops.json`` and ``rewards.json`` from the
`SunkwiBOT/twitch-drops-api <https://github.com/SunkwiBOT/twitch-drops-api>`_
GitHub repository and imports the data into the local database.

Usage::

    # Current data (latest commit)
    uv run python manage.py import_twitch_drops_api
    uv run python manage.py import_twitch_drops_api --source "SunkwiBOT/twitch-drops-api"

    # Historical data (all commits, oldest-first - captures expired campaigns)
    uv run python manage.py import_twitch_drops_api --historical
    uv run python manage.py import_twitch_drops_api --historical --max-commits 50
    uv run python manage.py import_twitch_drops_api --historical --git-dir /tmp/mirror

The ``--source`` argument controls the value stored in the
``data_source`` field on imported records (defaults to
``"SunkwiBOT/twitch-drops-api"``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import tempfile
from typing import TYPE_CHECKING
from typing import Any

import httpx
from colorama import Fore
from colorama import Style
from colorama import init as colorama_init
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from pydantic import ValidationError

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.schemas import SunkwiBotDropGroupSchema
from twitch.schemas import SunkwiBotRewardCampaignSchema
from twitch.utils import parse_date

if TYPE_CHECKING:
    from django.core.management.base import CommandParser
    from django.db.models import Model

    from twitch.schemas import SunkwiBotBenefitEdgeSchema
    from twitch.schemas import SunkwiBotDropCampaignACLSchema
    from twitch.schemas import SunkwiBotIndividualRewardSchema
    from twitch.schemas import SunkwiBotRewardSchema
    from twitch.schemas import SunkwiBotTimeBasedDropSchema

# Default raw GitHub URLs for the SunkwiBOT/twitch-drops-api repo
DEFAULT_DROPS_URL = (
    "https://raw.githubusercontent.com/SunkwiBOT/twitch-drops-api/main/drops.json"
)
DEFAULT_REWARDS_URL = (
    "https://raw.githubusercontent.com/SunkwiBOT/twitch-drops-api/main/rewards.json"
)

GIT_CLONE_URL = "https://github.com/SunkwiBOT/twitch-drops-api.git"

HTTP_TIMEOUT = 60  # seconds


class Command(BaseCommand):
    """Import drop and reward campaigns from the SunkwiBOT twitch-drops-api repo."""

    help = (
        "Import drop and reward campaigns from "
        "SunkwiBOT/twitch-drops-api (GitHub raw JSON)."
    )
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        """Populate the command with arguments."""
        parser.add_argument(
            "--source",
            type=str,
            default="SunkwiBOT/twitch-drops-api",
            help=(
                "Value to store in the data_source field. "
                "Default: SunkwiBOT/twitch-drops-api"
            ),
        )
        parser.add_argument(
            "--drops-url",
            type=str,
            default=DEFAULT_DROPS_URL,
            help=f"URL to fetch drops.json from. Default: {DEFAULT_DROPS_URL}",
        )
        parser.add_argument(
            "--rewards-url",
            type=str,
            default=DEFAULT_REWARDS_URL,
            help=f"URL to fetch rewards.json from. Default: {DEFAULT_REWARDS_URL}",
        )
        parser.add_argument(
            "--drops-only",
            action="store_true",
            help="Only import drops.json (skip rewards.json).",
        )
        parser.add_argument(
            "--rewards-only",
            action="store_true",
            help="Only import rewards.json (skip drops.json).",
        )
        parser.add_argument(
            "--crash-on-error",
            action="store_true",
            help="Crash on first validation/import error instead of continuing.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print per-record success messages.",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help=(
                "Compare SunkwiBOT data against the database without importing. "
                "Prints a summary of what's new, what exists, and field-level "
                "differences for overlapping campaigns."
            ),
        )
        parser.add_argument(
            "--merge",
            action="store_true",
            help=(
                "For existing campaigns, fill in empty/blank fields with "
                "SunkwiBOT data without overwriting populated ones. The "
                "original data_source is preserved."
            ),
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help=(
                "By default, campaigns already in the database are skipped "
                "(existing data_source is preserved). Use this flag to "
                "update existing campaigns with the incoming data instead."
            ),
        )
        parser.add_argument(
            "--historical",
            action="store_true",
            help=(
                "Iterate through all git commits (oldest first) to capture "
                "expired campaigns that are no longer in the latest JSON. "
                "Implies --skip-latest unless --no-skip-latest is given."
            ),
        )
        parser.add_argument(
            "--no-skip-latest",
            action="store_true",
            help="When used with --historical, also process the latest commit.",
        )
        parser.add_argument(
            "--max-commits",
            type=int,
            default=0,
            help="Maximum number of *unique-content* commits to process (0 = all).",
        )
        parser.add_argument(
            "--git-dir",
            type=str,
            default="",
            help=(
                "Path to a local bare clone of the repo. If not set, a temp "
                "clone is created and cleaned up."
            ),
        )
        parser.add_argument(
            "--from-file",
            type=str,
            default="",
            help=(
                "Path to a pre-extracted JSON file (e.g. drops_merged.json "
                "from tools/extract_historical_drops.py). Import this file "
                "directly instead of fetching from git history or URLs. "
                "Much faster than --historical for repeated imports."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:  # ruff:ignore[any-type, unused-method-argument, too-many-locals, too-many-statements]
        """Main entry point for the command.

        Raises:
            CommandError: If the --from-file path does not exist or is invalid.
        """
        colorama_init(autoreset=True)
        source: str = options["source"]
        verbose: bool = options["verbose"]
        crash_on_error: bool = options["crash_on_error"]
        report_mode: bool = options.get("report", False)
        merge_mode: bool = options.get("merge", False)
        skip_existing: bool = not options.get("update_existing")
        historical: bool = options.get("historical", False)

        # Caches for the duration of the command to avoid redundant DB lookups
        self._org_cache: dict[str, Organization] = {}
        self._game_cache: dict[str, Game] = {}
        self._channel_cache: dict[str, Channel] = {}
        self._benefit_cache: dict[str, DropBenefit] = {}

        # Merge mode: fill empty fields on existing campaigns instead of skipping
        self._merge_mode: bool = merge_mode
        if merge_mode:
            skip_existing = False

        drops_count = 0
        rewards_count = 0
        errors: list[str] = []

        if report_mode:
            self._generate_report(
                drops_url=options["drops_url"],
                rewards_url=options["rewards_url"],
                crash_on_error=crash_on_error,
            )
            return

        from_file: str = options.get("from_file", "") or ""

        if from_file:
            # Import from a pre-extracted merged JSON file (fast path).
            # The file is produced by tools/extract_historical_drops.py.
            from_file_path = pathlib.Path(from_file)
            if not from_file_path.exists():
                msg = f"File not found: {from_file}"
                raise CommandError(msg)

            with from_file_path.open(encoding="utf-8") as f:
                raw_data = json.load(f)

            if not isinstance(raw_data, list):
                msg = f"Expected a JSON array in {from_file}, got {type(raw_data).__name__}"
                raise CommandError(msg)

            # Detect file type by content structure
            is_drops = any("gameId" in item for item in raw_data)

            if is_drops:
                self.stdout.write(
                    f"Importing drops from {from_file} ({len(raw_data)} groups) …",
                )
                drops_count = self._parse_and_import_drops(
                    raw_data=raw_data,
                    source=source,
                    verbose=verbose,
                    crash_on_error=crash_on_error,
                    label=from_file_path.name,
                    skip_existing=skip_existing,
                )
            else:
                self.stdout.write(
                    f"Importing rewards from {from_file} ({len(raw_data)} campaigns) …",
                )
                rewards_count = self._parse_and_import_rewards(
                    raw_data=raw_data,
                    source=source,
                    verbose=verbose,
                    crash_on_error=crash_on_error,
                    label=from_file_path.name,
                    skip_existing=skip_existing,
                )
        elif historical:
            # Historical mode: clone repo and iterate commits
            hist_drops, hist_rewards, hist_errors = self._process_historical(
                source=source,
                verbose=verbose,
                crash_on_error=crash_on_error,
                skip_latest=not options["no_skip_latest"],
                max_commits=options["max_commits"],
                skip_existing=skip_existing,
                git_dir=options["git_dir"],
            )
            drops_count += hist_drops
            rewards_count += hist_rewards
            errors.extend(hist_errors)
        else:
            # Normal mode: fetch latest from URLs
            if not options["rewards_only"]:
                try:
                    drops_count += self._import_drops(
                        url=options["drops_url"],
                        source=source,
                        verbose=verbose,
                        crash_on_error=crash_on_error,
                        skip_existing=skip_existing,
                    )
                except Exception as exc:  # ruff:ignore[blind-except]
                    msg = f"Failed to import drops.json: {exc}"
                    self.stderr.write(self.style.ERROR(msg))
                    errors.append(msg)

            if not options["drops_only"]:
                try:
                    rewards_count += self._import_rewards(
                        url=options["rewards_url"],
                        source=source,
                        verbose=verbose,
                        crash_on_error=crash_on_error,
                        skip_existing=skip_existing,
                    )
                except Exception as exc:  # ruff:ignore[blind-except]
                    msg = f"Failed to import rewards.json: {exc}"
                    self.stderr.write(self.style.ERROR(msg))
                    errors.append(msg)

        # Summary
        self.stdout.write("\n" + "=" * 50)
        if historical:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Historical drops imported/updated: {drops_count}",
                ),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Historical rewards imported/updated: {rewards_count}",
                ),
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Drop campaigns imported/updated: {drops_count}",
                ),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Reward campaigns imported/updated: {rewards_count}",
                ),
            )
        if errors:
            for err in errors:
                self.stdout.write(self.style.ERROR(f"✗ {err}"))
        self.stdout.write("=" * 50)

    # ------------------------------------------------------------------
    # Historical import via git clone
    # ------------------------------------------------------------------

    def _process_historical(  # ruff:ignore[too-many-arguments]
        self,
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        skip_latest: bool,
        max_commits: int,
        git_dir: str,
        skip_existing: bool = False,
    ) -> tuple[int, int, list[str]]:
        """Clone the repo and iterate commits oldest-first to import historical data.

        Skips commits whose file content is byte-identical to the previously
        processed commit (deduplicates bot spam).

        Args:
            source: data_source label.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error.
            skip_latest: If True, skip the most recent commit (already imported
                by the non-historical path).
            max_commits: Max unique-content commits to process (0 = all).
            git_dir: Path to existing bare clone, or '' to create a temp one.
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Tuple of (drops_count, rewards_count, error_messages).
        """
        own_clone = not git_dir
        if own_clone:
            git_dir_obj = tempfile.mkdtemp(prefix="twitch-drops-api-")
            git_dir = git_dir_obj
            self.stdout.write(f"Cloning {GIT_CLONE_URL} …")
            self._git(
                git_dir,
                "clone",
                "--filter=blob:none",
                "--bare",
                GIT_CLONE_URL,
                git_dir,
            )
        else:
            self.stdout.write(f"Using existing clone at {git_dir} …")

        drops_count = 0
        rewards_count = 0
        errors: list[str] = []

        try:
            drops_count += self._import_historical_file(
                git_dir=git_dir,
                file_path="drops.json",
                source=source,
                verbose=verbose,
                crash_on_error=crash_on_error,
                skip_latest=skip_latest,
                max_commits=max_commits,
                skip_existing=skip_existing,
            )
            rewards_count += self._import_historical_file(
                git_dir=git_dir,
                file_path="rewards.json",
                source=source,
                verbose=verbose,
                crash_on_error=crash_on_error,
                skip_latest=skip_latest,
                max_commits=max_commits,
                skip_existing=skip_existing,
            )
        except Exception as exc:  # ruff:ignore[blind-except]
            errors.append(str(exc))
        finally:
            if own_clone:
                self._rmtree(git_dir)

        return drops_count, rewards_count, errors

    def _import_historical_file(  # ruff:ignore[too-many-arguments]
        self,
        git_dir: str,
        file_path: str,
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        skip_latest: bool,
        max_commits: int,
        skip_existing: bool,
    ) -> int:
        """Import all historical versions of a single file from git history.

        Args:
            git_dir: Path to bare clone.
            file_path: Path within repo (e.g. "drops.json").
            source: data_source label.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error.
            skip_latest: If True, skip the newest commit.
            max_commits: Max unique-content commits (0 = all).
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Number of campaigns imported/updated.
        """
        label = file_path.replace(".json", "")
        self.stdout.write(f"\nCollecting commits for {file_path} …")

        # Get all commits that touched this file, oldest first
        log_output = self._git(
            git_dir,
            "log",
            "--all",
            "--format=%H %ct",
            "--follow",
            "--reverse",
            "--",
            file_path,
        )
        lines = [ln.strip() for ln in log_output.strip().split("\n") if ln.strip()]
        total = len(lines)
        self.stdout.write(f"  Found {total} total commits for {file_path}")

        if skip_latest and total > 0:
            lines = lines[:-1]
            self.stdout.write(f"  Skipping latest commit, processing {len(lines)}")

        if max_commits and len(lines) > max_commits:
            lines = lines[:max_commits]
            self.stdout.write(f"  Limited to {max_commits} commits")

        if not lines:
            self.stdout.write(f"  No commits to process for {file_path}")
            return 0

        total_campaigns = 0
        seen_hashes: set[str] = set()
        processed = 0
        skipped_identical = 0

        for line in lines:
            parts = line.split(None, 1)
            if not parts:
                continue
            sha = parts[0]

            # Get file content at this commit
            try:
                content = self._git(
                    git_dir,
                    "show",
                    f"{sha}:{file_path}",
                    check=True,
                )
            except subprocess.CalledProcessError:
                # File didn't exist at this commit yet
                continue

            # Skip if content is byte-identical to last processed
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            if content_hash in seen_hashes:
                skipped_identical += 1
                continue
            seen_hashes.add(content_hash)

            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                if verbose:
                    self.stdout.write(
                        f"{Fore.YELLOW}→{Style.RESET_ALL} {sha[:7]}: "
                        f"invalid JSON, skipping",
                    )
                continue

            if not isinstance(data, list):
                continue

            short_sha = sha[:7]
            self.stdout.write(
                f"  [{processed + 1}/{len(lines)}] Importing {label} @ {short_sha} …",
            )

            if file_path == "drops.json":
                count = self._parse_and_import_drops(
                    raw_data=data,
                    source=source,
                    verbose=verbose,
                    crash_on_error=crash_on_error,
                    label=f"{label}@{short_sha}",
                    skip_existing=skip_existing,
                )
            else:
                count = self._parse_and_import_rewards(
                    raw_data=data,
                    source=source,
                    verbose=verbose,
                    crash_on_error=crash_on_error,
                    label=f"{label}@{short_sha}",
                    skip_existing=skip_existing,
                )

            total_campaigns += count
            processed += 1

        self.stdout.write(
            f"  Done: {processed} versions processed, "
            f"{skipped_identical} skipped (identical content), "
            f"{total_campaigns} total campaigns imported for {file_path}",
        )
        return total_campaigns

    # ------------------------------------------------------------------
    # Git / filesystem helpers
    # ------------------------------------------------------------------

    def _git(self, git_dir: str, *args: str, check: bool = True) -> str:
        """Run a git command in the bare clone directory.

        Args:
            git_dir: Path to the bare clone.
            *args: Git sub-command and arguments.
            check: If True, raise on non-zero exit.

        Returns:
            Stdout of the git command.
        """
        cmd = ["git", "--git-dir", git_dir, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)  # ruff:ignore[subprocess-without-shell-equals-true]
        return result.stdout

    def _rmtree(self, path: str) -> None:
        """Recursively remove a directory tree.

        Args:
            path: Path to remove.
        """
        try:
            for root_str, dirs, files in os.walk(path, topdown=False):
                root_path = pathlib.Path(root_str)
                for name in files:
                    (root_path / name).unlink()
                for name in dirs:
                    (root_path / name).rmdir()
            pathlib.Path(path).rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # drops.json import
    # ------------------------------------------------------------------
    def _fetch_json(self, url: str, label: str) -> list[dict[str, Any]]:
        """Fetch a JSON file from a URL and parse it into a list of dicts.

        Args:
            url: The URL to fetch.
            label: Human-readable label for error messages.

        Returns:
            Parsed JSON data as a list of dicts.

        Raises:
            TypeError: If the response is not a JSON array.
        """
        self.stdout.write(f"Fetching {label} from {url} …")
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
        data: Any = response.json()
        if not isinstance(data, list):
            msg = f"Expected a JSON array from {url}, got {type(data).__name__}"
            raise TypeError(msg)
        return data

    def _import_drops(
        self,
        url: str,
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        skip_existing: bool = False,
    ) -> int:
        """Import drop campaigns from a drops.json URL.

        Args:
            url: Raw GitHub URL for drops.json.
            source: Data source label to store on records.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error instead of continuing.
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Number of drop campaigns imported/updated.
        """
        raw_data = self._fetch_json(url, "drops.json")
        return self._parse_and_import_drops(
            raw_data=raw_data,
            source=source,
            verbose=verbose,
            crash_on_error=crash_on_error,
            label="drops.json",
            skip_existing=skip_existing,
        )

    @staticmethod
    def _strip_typename(data: Any) -> Any:  # ruff:ignore[any-type]
        """Recursively remove ``__typename`` keys from parsed JSON data.

        Old commits in the repo included GraphQL ``__typename`` metadata which
        the SunkwiBot schemas reject with ``extra="forbid"``.  Stripping them
        before validation keeps the schemas strict while still accepting
        historical payloads.

        Args:
            data: Raw parsed JSON (dict, list, or scalar).

        Returns:
            Data with all ``__typename`` keys removed.
        """
        if isinstance(data, dict):
            return {
                k: Command._strip_typename(v)
                for k, v in data.items()
                if k != "__typename"
            }
        if isinstance(data, list):
            return [Command._strip_typename(item) for item in data]
        return data

    def _generate_report(  # ruff:ignore[too-many-locals, too-many-statements]
        self,
        drops_url: str,
        rewards_url: str,
        *,
        crash_on_error: bool,
    ) -> None:
        """Compare SunkwiBOT data against the database without importing.

        Fetches drops.json and rewards.json, validates with schemas, then
        compares against the database to show:
        - How many campaigns are new
        - How many already exist
        - Field-level differences for overlapping campaigns
        - Total counts per source

        Args:
            drops_url: URL for drops.json.
            rewards_url: URL for rewards.json.
            crash_on_error: Raise on first error instead of continuing.

        Raises:
            ValidationError: If any item fails schema validation and crash_on_error is True.
        """
        from collections import Counter  # ruff:ignore[import-outside-top-level]

        self.stdout.write("=" * 60)
        self.stdout.write(self.style.SUCCESS("COMPARISON REPORT"))
        self.stdout.write(
            "Comparing SunkwiBOT data against local database…",
        )
        self.stdout.write("=" * 60)

        # Track differences for field-level comparison
        sample_limit = 5

        for label, raw_url, model_cls, id_field in [  # ruff:ignore[too-many-nested-blocks]
            ("drops", drops_url, DropCampaign, "twitch_id"),
            ("rewards", rewards_url, RewardCampaign, "twitch_id"),
        ]:
            self.stdout.write(f"\n--- {label.upper()} ---")
            try:
                raw_data = self._fetch_json(raw_url, f"{label}.json")
            except Exception as exc:  # ruff:ignore[blind-except]
                self.stderr.write(
                    self.style.ERROR(f"  Failed to fetch {label}.json: {exc}"),
                )
                continue

            # Validate
            validated_ids: set[str] = set()
            validated: list[Any] = []
            groups: list[SunkwiBotDropGroupSchema] = []
            if label == "drops":
                for i, raw_item in enumerate(raw_data):
                    try:
                        stripped_item = self._strip_typename(raw_item)
                        groups.append(
                            SunkwiBotDropGroupSchema.model_validate(stripped_item),
                        )
                    except ValidationError:
                        if crash_on_error:
                            raise
                        self.stdout.write(
                            f"  {Fore.YELLOW}⚠{Style.RESET_ALL} "
                            f"item [{i}] failed validation, skipping",
                        )
                for group in groups:
                    validated_ids.update(reward.twitch_id for reward in group.rewards)
            else:
                for i, raw_item in enumerate(raw_data):
                    try:
                        stripped_item = self._strip_typename(raw_item)
                        validated.append(
                            SunkwiBotRewardCampaignSchema.model_validate(stripped_item),
                        )
                    except ValidationError:
                        if crash_on_error:
                            raise
                        self.stdout.write(
                            f"  {Fore.YELLOW}⚠{Style.RESET_ALL} "
                            f"item [{i}] failed validation, skipping",
                        )
                validated_ids.update(c.twitch_id for c in validated)

            # Compare against DB
            existing_ids: set[str] = set(
                model_cls.objects.values_list(id_field, flat=True),
            )
            new_ids = validated_ids - existing_ids
            overlap_ids = validated_ids & existing_ids

            self.stdout.write(
                f"  Total in SunkwiBOT data:    {len(validated_ids)}",
            )
            self.stdout.write(
                f"  Total in local database:    {len(existing_ids)}",
            )
            self.stdout.write(
                f"  New (SunkwiBOT only):       {len(new_ids)}",
            )
            self.stdout.write(
                f"  Overlap (both):             {len(overlap_ids)}",
            )

            # Field-level comparison for overlapping campaigns
            if overlap_ids and label == "drops":
                field_diffs: list[tuple[str, str, str]] = []
                fields_to_check = [
                    "name",
                    "description",
                    "image_url",
                    "start_at",
                    "end_at",
                ]
                for group in groups:
                    for reward in group.rewards:
                        if reward.twitch_id not in overlap_ids:
                            continue
                        try:
                            db_obj = DropCampaign.objects.get(
                                twitch_id=reward.twitch_id,
                            )
                        except DropCampaign.DoesNotExist:
                            continue
                        for field in fields_to_check:
                            ours = getattr(db_obj, field, None)
                            theirs = getattr(reward, field, None)
                            # Compare string forms for dates
                            str_ours = str(ours) if ours is not None else ""
                            str_theirs = str(theirs) if theirs is not None else ""
                            if str_ours.strip() != str_theirs.strip():
                                field_diffs.append(
                                    (field, str_ours, str_theirs),
                                )

                if field_diffs:
                    diff_counter: Counter = Counter()
                    for diff_field, _ours_val, _theirs_val in field_diffs:
                        diff_counter[diff_field] += 1
                    self.stdout.write(
                        "\n  Field differences (overlapping campaigns):",
                    )
                    for field, count in diff_counter.most_common():
                        ours_empty = sum(
                            1 for f, o, _ in field_diffs if f == field and not o.strip()
                        )
                        theirs_empty = sum(
                            1
                            for f, _o, t in field_diffs
                            if f == field and not t.strip()
                        )
                        self.stdout.write(
                            f"    {field}: {count} campaigns differ "
                            f"(we have empty: {ours_empty}, "
                            f"they have empty: {theirs_empty})",
                        )
                    self.stdout.write(
                        "  Use --merge to fill empty fields from SunkwiBOT data.",
                    )
                else:
                    self.stdout.write(
                        "  ✓ No field differences in overlapping campaigns.",
                    )

            elif overlap_ids and label == "rewards":
                fields_to_check = [
                    "name",
                    "brand",
                    "summary",
                    "status",
                ]
                reward_diffs = 0
                for c_id in overlap_ids:
                    try:
                        db_obj = RewardCampaign.objects.get(twitch_id=c_id)
                    except RewardCampaign.DoesNotExist:
                        continue
                    # Re-find the schema entry
                    for c_entry in validated:
                        if c_entry.twitch_id == c_id:
                            for field in fields_to_check:
                                ours = getattr(db_obj, field, None)
                                theirs = getattr(c_entry, field, None)
                                if str(ours or "") != str(theirs or ""):
                                    reward_diffs += 1
                            break
                if reward_diffs:
                    self.stdout.write(
                        f"  {reward_diffs} field differences found in "
                        f"overlapping reward campaigns.",
                    )
                else:
                    self.stdout.write(
                        "  ✓ No field differences in overlapping reward campaigns.",
                    )

            if new_ids:
                self.stdout.write(
                    f"\n  {Fore.GREEN}Sample new campaigns:{Style.RESET_ALL}",
                )
                sample_new = list(new_ids)[:5]
                for sid in sample_new:
                    self.stdout.write(f"    - {sid}")
                if len(new_ids) > sample_limit:
                    self.stdout.write(
                        f"    … and {len(new_ids) - sample_limit} more",
                    )

        self.stdout.write("\n" + "=" * 60)

    def _parse_and_import_drops(  # ruff:ignore[too-many-arguments]
        self,
        raw_data: list[dict[str, Any]],
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        label: str = "drops.json",
        skip_existing: bool = False,
    ) -> int:
        """Validate and import raw drops.json data (from URL or git history).

        Args:
            raw_data: Parsed drops.json array.
            source: Data source label.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error.
            label: Human label for error messages (e.g. "drops.json@abc1234").
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Number of drop campaigns imported/updated.

        Raises:
            ValidationError: If crash_on_error is True and validation fails.
        """
        raw_data = self._strip_typename(raw_data)
        drop_groups: list[SunkwiBotDropGroupSchema] = []
        for i, item in enumerate(raw_data):
            try:
                group = SunkwiBotDropGroupSchema.model_validate(item)
                drop_groups.append(group)
            except ValidationError as e:
                msg = f"{label} item [{i}] failed validation: {e}"
                if crash_on_error:
                    raise
                self.stderr.write(self.style.ERROR(msg))
                continue

        total_campaigns = 0
        for group in drop_groups:
            # Get or create the shared Game for this group
            # Use the first reward's owner as the game's organization
            first_owner = group.rewards[0].owner if group.rewards else None
            org_obj = None
            if first_owner:
                org_obj = self._get_or_create_organization(
                    twitch_id=first_owner.twitch_id,
                    name=first_owner.name,
                    verbose=verbose,
                )

            game_obj = self._get_or_create_game(
                twitch_id=group.game_id,
                display_name=group.game_display_name,
                box_art_url=group.game_box_art_url,
                source=source,
                verbose=verbose,
                org_obj=org_obj,
            )

            for reward in group.rewards:
                try:
                    self._process_sunkwibot_reward(
                        reward=reward,
                        game_obj=game_obj,
                        source=source,
                        verbose=verbose,
                        skip_existing=skip_existing,
                    )
                    total_campaigns += 1
                except Exception as exc:
                    msg = (
                        f"Failed to import reward {reward.twitch_id} "
                        f"({reward.name}): {exc}"
                    )
                    if crash_on_error:
                        raise
                    self.stderr.write(self.style.ERROR(msg))

        return total_campaigns

    def _process_sunkwibot_reward(  # ruff:ignore[too-many-statements]
        self,
        reward: SunkwiBotRewardSchema,
        game_obj: Game,
        source: str,
        *,
        verbose: bool,
        skip_existing: bool = False,
    ) -> None:
        """Process a single reward item from drops.json into a DropCampaign.

        Args:
            reward: The validated reward schema from drops.json.
            game_obj: The Game instance for this campaign.
            source: Data source label.
            verbose: Print per-record messages.
            skip_existing: If True, skip if campaign already exists.
        """
        # Owner organization
        self._get_or_create_organization(
            twitch_id=reward.owner.twitch_id,
            name=reward.owner.name,
            verbose=verbose,
        )

        # Parse dates
        start_at_dt = parse_date(reward.start_at)
        end_at_dt = parse_date(reward.end_at)

        defaults: dict[str, Any] = {
            "name": reward.name,
            "description": reward.description,
            "image_url": reward.image_url,
            "game": game_obj,
            "start_at": start_at_dt,
            "end_at": end_at_dt,
            "details_url": reward.details_url,
            "account_link_url": reward.account_link_url,
            "data_source": source,
        }

        existing: DropCampaign | None = DropCampaign.objects.filter(
            twitch_id=reward.twitch_id,
        ).first()

        # Merge mode: fill empty fields, never overwrite populated ones
        if existing is not None and getattr(self, "_merge_mode", False):
            changed = False
            merge_fields: dict[str, str | object | None] = {
                "description": reward.description,
                "image_url": reward.image_url,
                "details_url": reward.details_url,
            }
            for field, new_value in merge_fields.items():
                old_value = getattr(existing, field, None)
                if not old_value and new_value:
                    setattr(existing, field, new_value)
                    changed = True
            if changed:
                existing.save(update_fields=list(merge_fields.keys()))
            # Track enrichment
            if source not in existing.operation_names:
                existing.operation_names.append(source)
                existing.save(update_fields=["operation_names"])
            if verbose:
                action = "Merged" if changed else "Skipped (merged)"
                self.stdout.write(
                    f"{Fore.CYAN}→{Style.RESET_ALL} {action} campaign: {reward.name}",
                )
            # Still process time-based drops and channels for existing
            if reward.time_based_drops:
                for tbd_schema in reward.time_based_drops:
                    self._process_time_based_drop(
                        tbd_schema=tbd_schema,
                        campaign_obj=existing,
                        source=source,
                        verbose=verbose,
                    )
            if reward.allow:
                self._process_allow_channels(
                    allow_schema=reward.allow,
                    campaign_obj=existing,
                    verbose=verbose,
                )
            if not existing.is_fully_imported:
                existing.is_fully_imported = True
                existing.save(update_fields=["is_fully_imported"])
            return

        if skip_existing and existing is not None:
            if verbose:
                self.stdout.write(
                    f"{Fore.YELLOW}→{Style.RESET_ALL} Skipped campaign: "
                    f"{reward.name} (already exists)",
                )
            # Still accumulate channels and time-based drops for skipped
            # campaigns so historical channels are never lost.
            if reward.time_based_drops:
                for tbd_schema in reward.time_based_drops:
                    self._process_time_based_drop(
                        tbd_schema=tbd_schema,
                        campaign_obj=existing,
                        source=source,
                        verbose=verbose,
                    )
            if reward.allow:
                self._process_allow_channels(
                    allow_schema=reward.allow,
                    campaign_obj=existing,
                    verbose=verbose,
                )
            if not existing.is_fully_imported:
                existing.is_fully_imported = True
                existing.save(update_fields=["is_fully_imported"])
            return

        campaign_obj, created = DropCampaign.objects.get_or_create(
            twitch_id=reward.twitch_id,
            defaults=defaults,
        )
        if not created:
            self._save_if_changed(campaign_obj, defaults)

        if verbose:
            action = "Created" if created else "Updated"
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} {action} campaign: "
                f"{reward.name} (source: {source})",
            )

        # Process time-based drops
        if reward.time_based_drops:
            for tbd_schema in reward.time_based_drops:
                self._process_time_based_drop(
                    tbd_schema=tbd_schema,
                    campaign_obj=campaign_obj,
                    source=source,
                    verbose=verbose,
                )

        # Process allowed channels
        if reward.allow:
            self._process_allow_channels(
                allow_schema=reward.allow,
                campaign_obj=campaign_obj,
                verbose=verbose,
            )

        # Mark as fully imported
        if not campaign_obj.is_fully_imported:
            campaign_obj.is_fully_imported = True
            campaign_obj.save(update_fields=["is_fully_imported"])

    def _process_time_based_drop(
        self,
        tbd_schema: SunkwiBotTimeBasedDropSchema,
        campaign_obj: DropCampaign,
        source: str,  # ruff:ignore[unused-method-argument]
        *,
        verbose: bool,
    ) -> None:
        """Process a single TimeBasedDrop from the SunkwiBOT schema.

        Args:
            tbd_schema: A SunkwiBotTimeBasedDropSchema instance.
            campaign_obj: The parent DropCampaign.
            source: Data source label.
            verbose: Print per-record messages.
        """
        start_at_dt = parse_date(tbd_schema.start_at)
        end_at_dt = parse_date(tbd_schema.end_at)

        drop_defaults: dict[str, Any] = {
            "campaign": campaign_obj,
            "name": tbd_schema.name,
            "required_subs": tbd_schema.required_subs,
        }
        if tbd_schema.required_minutes_watched is not None:
            drop_defaults["required_minutes_watched"] = (
                tbd_schema.required_minutes_watched
            )
        if start_at_dt is not None:
            drop_defaults["start_at"] = start_at_dt
        if end_at_dt is not None:
            drop_defaults["end_at"] = end_at_dt

        drop_obj, created = TimeBasedDrop.objects.get_or_create(
            twitch_id=tbd_schema.twitch_id,
            defaults=drop_defaults,
        )
        if not created:
            self._save_if_changed(drop_obj, drop_defaults)

        if verbose and created:
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created TimeBasedDrop: "
                f"{tbd_schema.name}",
            )

        # Process benefit edges
        for edge_schema in tbd_schema.benefit_edges:
            self._process_benefit_edge(
                edge_schema=edge_schema,
                drop_obj=drop_obj,
                verbose=verbose,
            )

    def _process_benefit_edge(
        self,
        edge_schema: SunkwiBotBenefitEdgeSchema,
        drop_obj: TimeBasedDrop,
        *,
        verbose: bool,
    ) -> None:
        """Process a benefit edge, creating/updating the benefit and edge.

        Args:
            edge_schema: A SunkwiBotBenefitEdgeSchema instance.
            drop_obj: The parent TimeBasedDrop.
            verbose: Print per-record messages.
        """
        benefit_schema = edge_schema.benefit

        # Get or create benefit
        cache_key = benefit_schema.twitch_id
        cached = self._benefit_cache.get(cache_key)
        if cached is not None:
            benefit_obj = cached
        else:
            benefit_obj = DropBenefit.objects.filter(
                twitch_id=benefit_schema.twitch_id,
            ).first()

        if benefit_obj is None:
            created_at_dt = (
                parse_date(benefit_schema.created_at)
                if benefit_schema.created_at
                else None
            )
            benefit_obj = DropBenefit.objects.create(
                twitch_id=benefit_schema.twitch_id,
                name=benefit_schema.name,
                image_asset_url=benefit_schema.image_asset_url,
                entitlement_limit=benefit_schema.entitlement_limit,
                is_ios_available=benefit_schema.is_ios_available,
                distribution_type=benefit_schema.distribution_type or "",
                created_at=created_at_dt,
            )
            if verbose:
                self.stdout.write(
                    f"{Fore.GREEN}✓{Style.RESET_ALL} Created DropBenefit: "
                    f"{benefit_schema.name}",
                )
        else:
            defaults: dict[str, Any] = {
                "name": benefit_schema.name,
                "image_asset_url": benefit_schema.image_asset_url,
                "entitlement_limit": benefit_schema.entitlement_limit,
                "is_ios_available": benefit_schema.is_ios_available,
                "distribution_type": benefit_schema.distribution_type or "",
            }
            if benefit_schema.created_at:
                created_at_dt = parse_date(benefit_schema.created_at)
                if created_at_dt:
                    defaults["created_at"] = created_at_dt
            self._save_if_changed(benefit_obj, defaults)

        self._benefit_cache[cache_key] = benefit_obj

        # Create or update the edge
        edge_defaults = {"entitlement_limit": edge_schema.entitlement_limit}
        edge_obj, edge_created = DropBenefitEdge.objects.get_or_create(
            drop=drop_obj,
            benefit=benefit_obj,
            defaults=edge_defaults,
        )
        if not edge_created:
            self._save_if_changed(edge_obj, edge_defaults)

    def _process_allow_channels(
        self,
        allow_schema: SunkwiBotDropCampaignACLSchema,
        campaign_obj: DropCampaign,
        *,
        verbose: bool,
    ) -> None:
        """Process the allow.channels list and set on the campaign.

        Args:
            allow_schema: A SunkwiBotDropCampaignACLSchema instance.
            campaign_obj: The DropCampaign to update.
            verbose: Print per-record messages.
        """
        # Update allow_is_enabled
        if campaign_obj.allow_is_enabled != allow_schema.is_enabled:
            campaign_obj.allow_is_enabled = allow_schema.is_enabled
            campaign_obj.save(update_fields=["allow_is_enabled"])

        if not allow_schema.channels:
            return

        channel_objects: list[Channel] = []
        for ch in allow_schema.channels:
            display_name: str = ch.display_name or ch.name
            channel_obj, created = Channel.objects.get_or_create(
                twitch_id=ch.twitch_id,
                defaults={
                    "name": ch.name,
                    "display_name": display_name,
                },
            )
            if not created:
                self._save_if_changed(
                    channel_obj,
                    {"name": ch.name, "display_name": display_name},
                )
            if verbose and created:
                self.stdout.write(
                    f"{Fore.GREEN}✓{Style.RESET_ALL} Created channel: {display_name}",
                )
            channel_objects.append(channel_obj)

        campaign_obj.allow_channels.add(*channel_objects)

    # ------------------------------------------------------------------
    # rewards.json import
    # ------------------------------------------------------------------

    def _import_rewards(
        self,
        url: str,
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        skip_existing: bool = False,
    ) -> int:
        """Import reward campaigns from a rewards.json URL.

        Args:
            url: Raw GitHub URL for rewards.json.
            source: Data source label to store on records.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error instead of continuing.
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Number of reward campaigns imported/updated.
        """
        raw_data = self._fetch_json(url, "rewards.json")
        return self._parse_and_import_rewards(
            raw_data=raw_data,
            source=source,
            verbose=verbose,
            crash_on_error=crash_on_error,
            label="rewards.json",
            skip_existing=skip_existing,
        )

    def _parse_and_import_rewards(  # ruff:ignore[too-many-arguments]
        self,
        raw_data: list[dict[str, Any]],
        source: str,
        *,
        verbose: bool,
        crash_on_error: bool,
        label: str = "rewards.json",
        skip_existing: bool = False,
    ) -> int:
        """Validate and import raw rewards.json data (from URL or git history).

        Args:
            raw_data: Parsed rewards.json array.
            source: Data source label.
            verbose: Print per-record messages.
            crash_on_error: Raise on first error.
            label: Human label for error messages.
            skip_existing: If True, skip campaigns already in the database.

        Returns:
            Number of reward campaigns imported/updated.

        Raises:
            ValidationError: If crash_on_error is True and validation fails.
        """
        raw_data = self._strip_typename(raw_data)
        validated: list[SunkwiBotRewardCampaignSchema] = []
        for i, item in enumerate(raw_data):
            try:
                campaign = SunkwiBotRewardCampaignSchema.model_validate(item)
                validated.append(campaign)
            except ValidationError as e:
                msg = f"{label} item [{i}] failed validation: {e}"
                if crash_on_error:
                    raise
                self.stderr.write(self.style.ERROR(msg))
                continue

        total = 0
        for campaign in validated:
            try:
                self._process_reward_campaign(
                    campaign=campaign,
                    source=source,
                    verbose=verbose,
                    skip_existing=skip_existing,
                )
                total += 1
            except Exception as exc:
                msg = (
                    f"Failed to import reward campaign {campaign.twitch_id} "
                    f"({campaign.name}): {exc}"
                )
                if crash_on_error:
                    raise
                self.stderr.write(self.style.ERROR(msg))

        return total

    def _process_reward_campaign(
        self,
        campaign: SunkwiBotRewardCampaignSchema,
        source: str,
        *,
        verbose: bool,
        skip_existing: bool = False,
    ) -> None:
        """Process a single reward campaign from rewards.json.

        Args:
            campaign: Validated reward campaign schema.
            source: Data source label.
            verbose: Print per-record messages.
            skip_existing: If True, skip if campaign already exists.
        """
        starts_at_dt = parse_date(campaign.starts_at)
        ends_at_dt = parse_date(campaign.ends_at)

        # Resolve game if present
        game_obj: Game | None = None
        if campaign.game and isinstance(campaign.game, dict):
            game_id = campaign.game.get("id")
            if game_id:
                with contextlib.suppress(Game.DoesNotExist):
                    game_obj = Game.objects.get(twitch_id=game_id)

        defaults: dict[str, Any] = {
            "name": campaign.name,
            "brand": campaign.brand,
            "starts_at": starts_at_dt,
            "ends_at": ends_at_dt,
            "status": campaign.status,
            "summary": campaign.summary,
            "instructions": campaign.instructions,
            "external_url": campaign.external_url,
            "reward_value_url_param": campaign.reward_value_url_param,
            "about_url": campaign.about_url,
            "is_sitewide": campaign.is_sitewide,
            "game": game_obj,
            "image_url": campaign.image.image1x_url if campaign.image else "",
            "data_source": source,
        }

        existing_reward: RewardCampaign | None = RewardCampaign.objects.filter(
            twitch_id=campaign.twitch_id,
        ).first()

        # Merge mode: fill empty fields, never overwrite populated ones
        if existing_reward is not None and getattr(self, "_merge_mode", False):
            changed = False
            merge_fields = {
                "summary": campaign.summary,
                "instructions": campaign.instructions,
                "image_url": campaign.image.image1x_url if campaign.image else "",
                "about_url": campaign.about_url,
                "external_url": campaign.external_url,
            }
            for field, new_value in merge_fields.items():
                old_value = getattr(existing_reward, field, None)
                if not old_value and new_value:
                    setattr(existing_reward, field, new_value)
                    changed = True
            if changed:
                existing_reward.save(
                    update_fields=list(merge_fields.keys()),
                )
            if verbose:
                action_str = "Merged" if changed else "Skipped (merged)"
                display_name = (
                    f"{campaign.brand}: {campaign.name}"
                    if campaign.brand
                    else campaign.name
                )
                self.stdout.write(
                    f"{Fore.CYAN}→{Style.RESET_ALL} {action_str} reward "
                    f"campaign: {display_name}",
                )
            # Still sync individual rewards (merge semantics: fill empty
            # fields, never overwrite populated ones).
            self._sync_reward_campaign_rewards(
                campaign_obj=existing_reward,
                reward_schemas=campaign.rewards,
                verbose=verbose,
                merge=True,
            )
            return

        if skip_existing and existing_reward is not None:
            if verbose:
                self.stdout.write(
                    f"{Fore.YELLOW}→{Style.RESET_ALL} Skipped reward campaign: "
                    f"{campaign.name} (already exists)",
                )
            # Still sync individual rewards so existing campaigns gain their
            # rewards on re-import (like the drop-campaign skip path which
            # keeps accumulating channels and time-based drops).
            self._sync_reward_campaign_rewards(
                campaign_obj=existing_reward,
                reward_schemas=campaign.rewards,
                verbose=verbose,
                merge=False,
            )
            return

        reward_obj, created = RewardCampaign.objects.get_or_create(
            twitch_id=campaign.twitch_id,
            defaults=defaults,
        )
        updated = self._save_if_changed(reward_obj, defaults) if not created else True

        if verbose and (created or updated):
            action = "Created" if created else "Updated"
            display_name = (
                f"{campaign.brand}: {campaign.name}"
                if campaign.brand
                else campaign.name
            )
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} {action} reward campaign: "
                f"{display_name} (source: {source})",
            )

        # Sync individual rewards; when updating an existing campaign the
        # incoming reward list is authoritative, so drop stale entries.
        self._sync_reward_campaign_rewards(
            campaign_obj=reward_obj,
            reward_schemas=campaign.rewards,
            verbose=verbose,
            merge=False,
            delete_stale=not created,
        )

    def _sync_reward_campaign_rewards(
        self,
        campaign_obj: RewardCampaign,
        reward_schemas: list[SunkwiBotIndividualRewardSchema],
        *,
        verbose: bool,
        merge: bool = False,
        delete_stale: bool = False,
    ) -> None:
        """Create/update the individual rewards belonging to a reward campaign.

        Args:
            campaign_obj: The parent RewardCampaign.
            reward_schemas: Validated individual rewards from rewards.json.
            verbose: Print per-record messages.
            merge: If True, only fill empty fields on existing rewards.
            delete_stale: If True, delete rewards that are no longer part of
                the incoming reward list (full update semantics).
        """
        incoming_ids: list[str] = []
        for reward_schema in reward_schemas:
            self._process_reward_campaign_reward(
                reward_schema=reward_schema,
                campaign_obj=campaign_obj,
                verbose=verbose,
                merge=merge,
            )
            incoming_ids.append(reward_schema.twitch_id)

        if delete_stale and incoming_ids:
            campaign_obj.rewards.exclude(twitch_id__in=incoming_ids).delete()  # pyright: ignore[reportAttributeAccessIssue]

    def _process_reward_campaign_reward(
        self,
        reward_schema: SunkwiBotIndividualRewardSchema,
        campaign_obj: RewardCampaign,
        *,
        verbose: bool,
        merge: bool = False,
    ) -> None:
        """Create or update a single reward inside a reward campaign.

        Args:
            reward_schema: A validated individual reward schema.
            campaign_obj: The parent RewardCampaign.
            verbose: Print per-record messages.
            merge: If True, only fill empty fields on existing rewards.
        """
        earnable_until_dt = (
            parse_date(reward_schema.earnable_until)
            if reward_schema.earnable_until
            else None
        )

        defaults: dict[str, Any] = {
            "reward_campaign": campaign_obj,
            "name": reward_schema.name,
            "banner_image_url": (
                reward_schema.banner_image.image1x_url
                if reward_schema.banner_image
                else ""
            ),
            "thumbnail_image_url": (
                reward_schema.thumbnail_image.image1x_url
                if reward_schema.thumbnail_image
                else ""
            ),
            "earnable_until": earnable_until_dt,
            "redemption_instructions": reward_schema.redemption_instructions,
            "redemption_url": reward_schema.redemption_url,
        }

        existing = Reward.objects.filter(
            twitch_id=reward_schema.twitch_id,
        ).first()

        # Merge mode: fill empty fields, never overwrite populated ones
        if existing is not None and merge:
            changed = False
            merge_fields = [field for field in defaults if field != "reward_campaign"]
            for field in merge_fields:
                new_value = defaults[field]
                old_value = getattr(existing, field, None)
                if not old_value and new_value:
                    setattr(existing, field, new_value)
                    changed = True
            if changed:
                existing.save(update_fields=merge_fields)
            if verbose:
                action_str = "Merged" if changed else "Skipped (merged)"
                self.stdout.write(
                    f"{Fore.CYAN}→{Style.RESET_ALL} {action_str} reward: "
                    f"{reward_schema.name}",
                )
            return

        reward_obj, created = Reward.objects.get_or_create(
            twitch_id=reward_schema.twitch_id,
            defaults=defaults,
        )
        if not created:
            self._save_if_changed(reward_obj, defaults)

        if verbose and created:
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created reward: {reward_schema.name}",
            )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _save_if_changed(self, obj: Model, defaults: dict[str, Any]) -> bool:  # type: ignore[valid-type]
        """Save the model instance only when data actually changed.

        Args:
            obj: The model instance to potentially update.
            defaults: Field values to apply.

        Returns:
            True if the object was saved, False if no changes were detected.
        """
        changed_fields: list[str] = []
        for field, new_value in defaults.items():
            if getattr(obj, field, None) != new_value:
                setattr(obj, field, new_value)
                changed_fields.append(field)

        if not changed_fields:
            return False

        obj.save(update_fields=changed_fields)
        return True

    def _get_or_create_organization(
        self,
        twitch_id: str,
        name: str,
        *,
        verbose: bool,
    ) -> Organization:
        """Get or create an organization.

        Args:
            twitch_id: Twitch organization ID.
            name: Organization name.
            verbose: Print per-record messages.

        Returns:
            Organization instance.
        """
        cached = self._org_cache.get(twitch_id)
        if cached is not None:
            self._save_if_changed(cached, {"name": name})
            return cached

        org_obj, created = Organization.objects.get_or_create(
            twitch_id=twitch_id,
            defaults={"name": name},
        )
        if not created:
            self._save_if_changed(org_obj, {"name": name})
        if verbose and created:
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created organization: {name}",
            )

        self._org_cache[twitch_id] = org_obj
        return org_obj

    def _get_or_create_game(  # ruff:ignore[too-many-arguments]
        self,
        twitch_id: str,
        display_name: str,
        box_art_url: str,
        source: str,  # ruff:ignore[unused-method-argument]
        *,
        verbose: bool,
        org_obj: Organization | None = None,
    ) -> Game:
        """Get or create a game.

        Args:
            twitch_id: Twitch game ID.
            display_name: Game display name.
            box_art_url: Box art URL.
            source: Data source label.
            verbose: Print per-record messages.
            org_obj: Optional organization to assign as owner of the game.

        Returns:
            Game instance.
        """
        cached = self._game_cache.get(twitch_id)
        if cached is not None:
            self._save_if_changed(
                cached,
                {
                    "display_name": display_name,
                    "box_art": box_art_url,
                },
            )
            return cached

        defaults: dict[str, Any] = {
            "display_name": display_name,
            "name": display_name,
            "box_art": box_art_url,
        }
        game_obj, created = Game.objects.get_or_create(
            twitch_id=twitch_id,
            defaults=defaults,
        )
        if not created:
            self._save_if_changed(
                game_obj,
                {
                    "display_name": display_name,
                    "name": display_name,
                    "box_art": box_art_url,
                },
            )
        # Assign owner organization if provided
        if (
            org_obj is not None
            and not game_obj.owners.filter(
                pk=org_obj.pk,
            ).exists()
        ):
            game_obj.owners.add(org_obj)
            if verbose:
                self.stdout.write(
                    f"{Fore.GREEN}✓{Style.RESET_ALL} Assigned owner "
                    f"{org_obj.name} to game {display_name}",
                )
        if verbose and created:
            self.stdout.write(
                f"{Fore.GREEN}✓{Style.RESET_ALL} Created game: {display_name}",
            )

        self._game_cache[twitch_id] = game_obj
        return game_obj
