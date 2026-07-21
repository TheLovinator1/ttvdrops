#!/usr/bin/env python3
"""Extract all unique historical versions of drops.json/rewards.json from the SunkwiBOT/twitch-drops-api repo and merge them into a single JSON file.

For each campaign (identified by its ``id``), channels from ALL commits are
accumulated and deduplicated.  The output file can be imported in a single
ORM pass via ``--from-file``, which is much faster than iterating 14k+
git commits through Django.

Usage:
    python tools/extract_historical_drops.py [--rewards] [--git-dir DIR] [--output FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import tempfile
from pathlib import Path
from typing import Any

REPO_URL = "https://github.com/SunkwiBOT/twitch-drops-api"

# Fields that should be taken from the *first* occurrence of a campaign
# (game metadata, names, descriptions, timestamps, etc.)
CAMPAIGN_FIRST_FIELDS = {
    "gameId",
    "gameDisplayName",
    "gameBoxArtURL",
    "startAt",
    "endAt",
}

# Fields within individual rewards that should be first-occurrence only
REWARD_FIRST_FIELDS: set[str] = set()

# Fields that should be *merged* across all occurrences (channels, time-based drops)
REWARD_MERGE_FIELDS = {"channels"}  # mapped from allow.channels


def run_git(git_dir: str, *args: str, check: bool = True) -> str:
    """Run a git command and return stdout.

    Args:
        git_dir: Path to the git repository.
        *args: Git command arguments.
        check: If True, raise on non-zero exit.

    Returns:
        The stdout output of the git command.

    Raises:
        subprocess.CalledProcessError: If the command fails and check is True.
        FileNotFoundError: If the git executable is not found.
    """
    try:
        git: str | None = shutil.which("git")
        if not git:
            msg = "Git executable not found in PATH."
            raise FileNotFoundError(msg)
        result: subprocess.CompletedProcess[str] = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
            [git, "--git-dir", git_dir, *args],
            capture_output=True,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError:
        if check:
            raise
        return ""
    else:
        return result.stdout


def extract(
    git_dir: str,
    file_path: str,
    *,
    skip_latest: bool = True,
) -> list[dict[str, Any]]:
    """Extract and merge all historical versions of *file_path* from the repo.

    Returns:
        A list of top-level entries (drop groups or reward campaigns)
        with channels accumulated across all commits.
    """
    log_output = run_git(
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

    if skip_latest and lines:
        lines = lines[:-1]

    # Per-campaign-id accumulators
    # Structure: {twitch_id: {"_first_data": {...}, "_channels": {channel_id: channel_data}}}
    campaigns: dict[str, dict[str, Any]] = {}
    # For drops.json, track groups separately
    groups: dict[str, dict[str, Any]] = {}
    total_processed = 0
    seen_hashes: set[str] = set()

    for line in lines:
        sha = line.split(None, 1)[0]

        try:
            content = run_git(git_dir, "show", f"{sha}:{file_path}", check=True)
        except subprocess.CalledProcessError:
            continue

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue

        if not isinstance(data, list):
            continue

        if file_path == "drops.json":
            _merge_drops_version(data, groups, campaigns)
        else:
            _merge_rewards_version(data, campaigns)

        total_processed += 1

    # Build final output from accumulated data
    result: list[dict[str, Any]] = []

    if file_path == "drops.json":
        # Sort groups by their startAt for stable output
        sorted_groups = sorted(groups.values(), key=lambda g: g.get("startAt", ""))
        for group in sorted_groups:
            group_rewards = [
                _build_reward_output(campaigns[cid])
                for cid in group.get("_reward_ids", [])
                if cid in campaigns
            ]
            if group_rewards:
                entry = {k: v for k, v in group.items() if not k.startswith("_")}
                entry["rewards"] = group_rewards
                result.append(entry)
    else:
        sorted_campaigns = sorted(
            campaigns.values(),
            key=lambda c: c.get("_first_data", {}).get("startAt", ""),
        )
        result.extend(_build_reward_output(camp) for camp in sorted_campaigns)

    return result


def _merge_drops_version(
    data: list[dict[str, Any]],
    groups: dict[str, dict[str, Any]],
    campaigns: dict[str, dict[str, Any]],
) -> None:
    """Merge a single commit's drops.json data into the accumulators."""
    for item in data:
        game_id = item.get("gameId", "")
        if game_id not in groups:
            groups[game_id] = {k: v for k, v in item.items() if k != "rewards"}
            groups[game_id]["_reward_ids"] = []

        for reward in item.get("rewards", []):
            rid = reward.get("id", "")
            if not rid:
                continue

            if rid not in campaigns:
                # First occurrence: store all fields
                campaigns[rid] = {
                    "_first_data": dict(reward),
                    "_channels": {},
                }
                groups[game_id]["_reward_ids"].append(rid)
            else:
                # Subsequent occurrence: merge metadata + channels
                _merge_metadata(campaigns[rid], reward)
                _merge_channels(campaigns[rid], reward)

            # Always merge channels from every commit
            _merge_channels(campaigns[rid], reward)


def _merge_rewards_version(
    data: list[dict[str, Any]],
    campaigns: dict[str, dict[str, Any]],
) -> None:
    """Merge a single commit's rewards.json data into the accumulators."""
    for reward in data:
        rid = reward.get("id", "")
        if not rid:
            continue

        if rid not in campaigns:
            campaigns[rid] = {
                "_first_data": dict(reward),
                "_channels": {},
            }
        else:
            # Subsequent occurrence: merge metadata
            _merge_metadata(campaigns[rid], reward)

        _merge_channels(campaigns[rid], reward)


def _merge_metadata(
    campaign: dict[str, Any],
    reward_data: dict[str, Any],
) -> None:
    """Fill empty/missing fields in ``_first_data`` with values from later commits.

    For each top-level field in *reward_data*, if the stored value in
    ``_first_data`` is empty/None and the new value is not, update it.
    This ensures campaigns get the best available description, dates,
    image URL, etc. across all historical versions.
    """
    first = campaign["_first_data"]

    # Top-level string/primitive fields to merge (first non-empty wins)
    for key in (
        "name",
        "description",
        "imageURL",
        "detailsURL",
        "accountLinkURL",
        "startAt",
        "endAt",
        "status",
    ):
        old_val = first.get(key)
        new_val = reward_data.get(key)
        if not old_val and new_val:
            first[key] = new_val

    # Nested fields: allow.isEnabled
    old_allow = first.get("allow")
    new_allow = reward_data.get("allow")
    if (
        isinstance(old_allow, dict)
        and isinstance(new_allow, dict)
        and not old_allow.get("isEnabled")
        and new_allow.get("isEnabled")
    ):
        old_allow["isEnabled"] = new_allow["isEnabled"]


def _merge_channels(
    campaign: dict[str, Any],
    reward_data: dict[str, Any],
) -> None:
    """Merge channels from *reward_data* into the accumulated *campaign*."""
    allow = reward_data.get("allow")
    if not allow or not isinstance(allow, dict):
        return
    channels = allow.get("channels")
    if not channels or not isinstance(channels, list):
        return

    for ch in channels:
        ch_id = ch.get("id", "")
        if ch_id and ch_id not in campaign["_channels"]:
            campaign["_channels"][ch_id] = dict(ch)


def _build_reward_output(campaign: dict[str, Any]) -> dict[str, Any]:
    """Build the final reward dict from accumulated data.

    Returns:
        The merged reward dict with all accumulated channels.
    """
    first = campaign["_first_data"]

    # Start with all first-occurrence data
    result = dict(first)

    # Build the allow field with merged channels
    merged_channels = list(campaign["_channels"].values())
    if merged_channels or "allow" in first:
        allow = (
            dict(first.get("allow", {})) if isinstance(first.get("allow"), dict) else {}
        )
        allow["channels"] = merged_channels
        allow["isEnabled"] = (
            first.get("allow", {}).get("isEnabled", True)
            if isinstance(first.get("allow"), dict)
            else True
        )
        result["allow"] = allow

    return result


def main() -> None:
    """Entry point: clone repo, extract historical data, write output JSON."""
    parser = argparse.ArgumentParser(
        description="Extract historical drops data from SunkwiBOT/twitch-drops-api",
    )
    parser.add_argument(
        "--rewards",
        action="store_true",
        help="Extract rewards.json instead of drops.json",
    )
    parser.add_argument(
        "--git-dir",
        type=str,
        default="",
        help="Path to an existing bare clone (avoids re-cloning)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output file path (default: drops_merged.json or rewards_merged.json)",
    )
    args = parser.parse_args()

    file_path = "rewards.json" if args.rewards else "drops.json"
    default_output = "rewards_merged.json" if args.rewards else "drops_merged.json"
    output_path = args.output or default_output

    git_dir = args.git_dir
    cleanup = False
    tmp: Path | None = None

    if not git_dir:
        tmp = Path(tempfile.mkdtemp())
        run_git(
            str(tmp),
            "clone",
            "--filter=blob:none",
            "--bare",
            REPO_URL,
            str(tmp / "repo.git"),
            check=True,
        )
        git_dir = str(tmp / "repo.git")
        cleanup = True

    try:
        result = extract(git_dir, file_path)
        with Path(output_path).open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    finally:
        if cleanup and tmp is not None:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    main()
