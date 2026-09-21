#!/usr/bin/env python3
"""Publish an RSS feed when Zephyr introduces a new SoC family.

The detector is deliberately structural.  It compares the complete set of
``family[].name`` entries in Zephyr's ``soc/**/soc.yml`` files at consecutive
first-parent commits.  Commit subjects are explanatory metadata only; they do
not decide whether an item is published.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised before the CLI can run
    raise SystemExit(
        "PyYAML is required; install the dependencies before running"
    ) from exc


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
ATOM_NS = "http://www.w3.org/2005/Atom"
DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "repo": "upstream/zephyr",
    "revision": "HEAD",
    "catalog_file": "state/catalog.json",
    "cursor_file": "state/cursor.json",
    "history_file": "data/items.json",
    "feed_file": "public/feed.xml",
    "feed_title": "New Zephyr SoC families",
    "feed_description": "New SoC families added to the Zephyr Project",
    "feed_link": "https://example.invalid/feed.xml",
    "repository_url": "https://github.com/zephyrproject-rtos/zephyr",
    "max_feed_items": 50,
    "backfill_days": 365,
}
ALLOWED_CONFIG_KEYS = frozenset(DEFAULT_CONFIG)


class FeedError(RuntimeError):
    """A validation or upstream-read failure that must not advance state."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def isoformat(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise FeedError("timestamp is missing its timezone")
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str, *, context: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise FeedError(f"{context} must be a non-empty timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedError(f"{context} is not a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FeedError(f"{context} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def require_http_url(value: Any, *, key: str) -> str:
    if not isinstance(value, str):
        raise FeedError(f"config key {key!r} must be a string")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedError(f"config key {key!r} must be an absolute HTTP(S) URL")
    return value.rstrip("/") if key == "repository_url" else value


def load_config(
    config_path: Path, repo_override: str | None, revision_override: str | None
) -> dict[str, Any]:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedError(f"could not read config: {exc}") from exc
    if not isinstance(raw, dict):
        raise FeedError("config must be a JSON object")
    unknown = sorted(set(raw) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise FeedError(f"unknown config key(s): {', '.join(unknown)}")

    config = {**DEFAULT_CONFIG, **raw}
    base = config_path.resolve().parent

    if config["schema_version"] != SCHEMA_VERSION:
        raise FeedError("config has an unsupported schema_version")

    for key in (
        "repo",
        "revision",
        "catalog_file",
        "cursor_file",
        "history_file",
        "feed_file",
    ):
        if not isinstance(config[key], str) or not config[key].strip():
            raise FeedError(f"config key {key!r} must be a non-empty string")
    for key in ("feed_title", "feed_description"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise FeedError(f"config key {key!r} must be a non-empty string")

    if isinstance(config["max_feed_items"], bool) or not isinstance(
        config["max_feed_items"], int
    ):
        raise FeedError("config key 'max_feed_items' must be an integer")
    if not 1 <= config["max_feed_items"] <= 1000:
        raise FeedError("config key 'max_feed_items' must be between 1 and 1000")
    if isinstance(config["backfill_days"], bool) or not isinstance(
        config["backfill_days"], int
    ):
        raise FeedError("config key 'backfill_days' must be an integer")
    if not 0 <= config["backfill_days"] <= 3650:
        raise FeedError("config key 'backfill_days' must be between 0 and 3650")

    config["feed_link"] = require_http_url(config["feed_link"], key="feed_link")
    config["repository_url"] = require_http_url(
        config["repository_url"], key="repository_url"
    )
    if repo_override:
        config["repo"] = str(Path(repo_override).resolve())
    else:
        config["repo"] = str((base / config["repo"]).resolve())
    if revision_override:
        config["revision"] = revision_override
    for key in ("catalog_file", "cursor_file", "history_file", "feed_file"):
        config[key] = str((base / config[key]).resolve())
    return config


class GitRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.is_dir():
            raise FeedError("Zephyr repository path is not a directory")
        inside = self.run_text("rev-parse", "--is-inside-work-tree").strip()
        if inside != "true":
            raise FeedError("Zephyr repository path is not a Git working tree")
        shallow = self.run_text("rev-parse", "--is-shallow-repository").strip()
        if shallow != "false":
            raise FeedError("a complete, non-shallow Zephyr checkout is required")

    def run_bytes(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.path), *args],
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise FeedError(f"could not execute Git: {exc}") from exc
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise FeedError(
                f"Git command failed ({' '.join(args)}): {detail or 'unknown error'}"
            )
        return result

    def run_text(self, *args: str, check: bool = True) -> str:
        output = self.run_bytes(*args, check=check).stdout
        try:
            return output.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise FeedError(
                f"Git returned non-UTF-8 output for {' '.join(args)}"
            ) from exc

    def resolve_commit(self, revision: str) -> str:
        value = (
            self.run_text("rev-parse", "--verify", f"{revision}^{{commit}}")
            .strip()
            .lower()
        )
        if not SHA_RE.fullmatch(value):
            raise FeedError("Git resolved an invalid commit identifier")
        return value

    def first_parent(self, commit: str) -> str:
        fields = (
            self.run_text("rev-list", "--parents", "-n", "1", commit).strip().split()
        )
        if not fields or fields[0].lower() != commit:
            raise FeedError(f"could not read parents for commit {commit}")
        if len(fields) < 2:
            raise FeedError(
                "backfill reaches the repository root; choose a shorter backfill window"
            )
        return fields[1].lower()

    def first_parent_commits(
        self,
        range_spec: str,
        *,
        since: dt.datetime | None = None,
        pathspecs: Sequence[str] = (),
    ) -> list[str]:
        args = ["rev-list", "--first-parent", "--reverse"]
        if since is not None:
            args.append(f"--since={isoformat(since)}")
        args.append(range_spec)
        if pathspecs:
            args.append("--")
            args.extend(pathspecs)
        commits = [
            line.strip().lower()
            for line in self.run_text(*args).splitlines()
            if line.strip()
        ]
        if any(not SHA_RE.fullmatch(commit) for commit in commits):
            raise FeedError("Git returned an invalid commit while enumerating history")
        return commits

    def ensure_first_parent_ancestor(self, baseline: str, target: str) -> None:
        if baseline == target:
            return
        raw_count = self.run_text(
            "rev-list", "--first-parent", "--count", f"{baseline}..{target}"
        ).strip()
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise FeedError(
                "Git returned an invalid first-parent commit count"
            ) from exc
        if count <= 0:
            raise FeedError("cursor is not on the target's first-parent history")
        ancestor = self.resolve_commit(f"{target}~{count}")
        if ancestor != baseline:
            raise FeedError("cursor is not on the target's first-parent history")

    def changed_soc_yml(self, parent: str, commit: str) -> dict[str, str]:
        output = self.run_text(
            "diff", "--name-status", "--no-renames", parent, commit, "--", "soc"
        )
        changes: dict[str, str] = {}
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) != 2 or not fields[0]:
                raise FeedError(f"commit {commit} returned malformed changed-path data")
            status, path = fields
            if PurePosixPath(path).name != "soc.yml":
                continue
            if status not in {"A", "M", "D", "T"}:
                raise FeedError(
                    f"commit {commit} has unsupported Git status {status!r}"
                )
            changes[path] = status
        if not changes:
            raise FeedError(
                f"path-filtered commit {commit} did not change a soc.yml file"
            )
        return changes

    def soc_yml_paths(self, revision: str) -> list[str]:
        raw = self.run_bytes(
            "ls-tree", "-r", "--name-only", "-z", revision, "--", "soc"
        ).stdout
        try:
            paths = [
                part.decode("utf-8", errors="strict")
                for part in raw.split(b"\0")
                if part
            ]
        except UnicodeDecodeError as exc:
            raise FeedError(f"tree {revision} contains a non-UTF-8 path") from exc
        selected = sorted(
            path for path in paths if PurePosixPath(path).name == "soc.yml"
        )
        if not selected:
            raise FeedError(f"tree {revision} contains no soc.yml metadata")
        return selected

    def read_blob(self, revision: str, path: str) -> bytes:
        return self.run_bytes("show", f"{revision}:{path}").stdout

    def commit_metadata(self, commit: str) -> dict[str, str]:
        raw = self.run_text("show", "-s", "--format=%H%x00%cI%x00%s", commit).rstrip(
            "\n"
        )
        fields = raw.split("\0")
        if len(fields) != 3 or fields[0].lower() != commit:
            raise FeedError(f"could not read complete metadata for commit {commit}")
        published = isoformat(
            parse_timestamp(fields[1], context=f"commit {commit} date")
        )
        subject = " ".join(fields[2].split())
        if not subject:
            raise FeedError(f"commit {commit} has an empty subject")
        return {"published": published, "commit_subject": subject}


def _mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FeedError(f"{context} must be a mapping")
    return value


def _sequence(value: Any, *, context: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise FeedError(f"{context} must be a list")
    return value


def _name(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise FeedError(f"{context} must be a non-empty, trimmed string")
    return value


def _named_entries(
    value: Any, *, context: str, allow_duplicate_names: bool = False
) -> list[Mapping[str, Any]]:
    entries = _sequence(value, context=context)
    parsed: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(entries):
        item = _mapping(raw, context=f"{context}[{index}]")
        name = _name(item.get("name"), context=f"{context}[{index}].name")
        if name in seen and not allow_duplicate_names:
            raise FeedError(f"{context} repeats name {name!r}")
        seen.add(name)
        parsed.append(item)
    return parsed


def parse_soc_yml(content: bytes, *, path: str) -> dict[str, Any]:
    if not content.strip():
        raise FeedError(f"{path} is empty")
    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FeedError(f"{path} is not UTF-8") from exc
    try:
        document = yaml.safe_load(decoded)
    except yaml.YAMLError as exc:
        raise FeedError(f"could not parse {path}: {exc}") from exc
    top = _mapping(document, context=path)

    all_members: set[str] = set()

    # Before the current family-based model, valid soc.yml files often exposed
    # series or SoCs directly at the top level.  They are not feed families,
    # but they are essential evidence: wrapping existing hardware in a newly
    # named family is a metadata refactor, not a new hardware-family event.
    for series_index, series in enumerate(
        _named_entries(top.get("series", []), context=f"{path}.series")
    ):
        series_name = _name(
            series.get("name"), context=f"{path}.series[{series_index}].name"
        )
        all_members.add(f"series:{series_name}")
        for soc in _named_entries(
            series.get("socs", []), context=f"{path}.series[{series_index}].socs"
        ):
            all_members.add(f"soc:{_name(soc.get('name'), context=f'{path} SoC name')}")
    for soc in _named_entries(top.get("socs", []), context=f"{path}.socs"):
        all_members.add(f"soc:{_name(soc.get('name'), context=f'{path} SoC name')}")

    raw_families = top.get("family")
    if raw_families is None:
        return {"families": {}, "member_tokens": sorted(all_members)}
    # Zephyr intentionally permits a family name to appear in multiple family
    # entries (for example, to group different series blocks).  Family identity
    # is the name, so those entries are unioned below.
    families = _named_entries(
        raw_families, context=f"{path}.family", allow_duplicate_names=True
    )
    accumulated: dict[str, dict[str, set[str]]] = {}
    for family_index, family in enumerate(families):
        family_name = _name(
            family.get("name"), context=f"{path}.family[{family_index}].name"
        )
        series_names: list[str] = []
        soc_names: list[str] = []

        raw_series = family.get("series", [])
        for series_index, series in enumerate(
            _named_entries(raw_series, context=f"{path}.family[{family_index}].series")
        ):
            series_name = _name(
                series.get("name"),
                context=f"{path}.family[{family_index}].series[{series_index}].name",
            )
            series_names.append(series_name)
            for soc in _named_entries(
                series.get("socs", []),
                context=f"{path}.family[{family_index}].series[{series_index}].socs",
            ):
                soc_names.append(_name(soc.get("name"), context=f"{path} SoC name"))

        for soc in _named_entries(
            family.get("socs", []), context=f"{path}.family[{family_index}].socs"
        ):
            soc_names.append(_name(soc.get("name"), context=f"{path} SoC name"))

        if len(soc_names) != len(set(soc_names)):
            raise FeedError(f"family {family_name!r} in {path} repeats a SoC name")
        if not series_names and not soc_names:
            raise FeedError(f"family {family_name!r} in {path} has no series or SoCs")
        merged = accumulated.setdefault(family_name, {"series": set(), "socs": set()})
        merged["series"].update(series_names)
        merged["socs"].update(soc_names)
        all_members.update(f"series:{name}" for name in series_names)
        all_members.update(f"soc:{name}" for name in soc_names)
    families_result = {
        family_name: {
            "series": sorted(details["series"]),
            "socs": sorted(details["socs"]),
            "source_files": [path],
        }
        for family_name, details in sorted(accumulated.items())
    }
    return {"families": families_result, "member_tokens": sorted(all_members)}


def load_catalog_by_path(
    repo: GitRepository, revision: str
) -> dict[str, dict[str, Any]]:
    return {
        path: parse_soc_yml(repo.read_blob(revision, path), path=path)
        for path in repo.soc_yml_paths(revision)
    }


def merge_catalog_by_path(
    by_path: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(by_path):
        parsed = _mapping(
            by_path[path].get("families"), context=f"{path} parsed families"
        )
        for family_name, details in parsed.items():
            if family_name not in catalog:
                # Copy lists so merging a later declaration never mutates the
                # per-path evidence used for incremental verification.
                catalog[family_name] = {
                    "series": list(details["series"]),
                    "socs": list(details["socs"]),
                    "source_files": list(details["source_files"]),
                }
                continue
            # A family may also be split across multiple metadata files.  Merge
            # by canonical name instead of losing or rejecting valid members.
            existing = catalog[family_name]
            existing["series"] = sorted(
                set(existing["series"]) | set(details["series"])
            )
            existing["socs"] = sorted(set(existing["socs"]) | set(details["socs"]))
            existing["source_files"] = sorted(
                set(existing["source_files"]) | set(details["source_files"])
            )
    if not catalog:
        raise FeedError("SoC metadata contains no family declarations")
    return {name: catalog[name] for name in sorted(catalog)}


def all_member_tokens(by_path: Mapping[str, Mapping[str, Any]]) -> set[str]:
    tokens: set[str] = set()
    for path, parsed in by_path.items():
        raw_tokens = _sequence(
            parsed.get("member_tokens"), context=f"{path} member tokens"
        )
        if not all(isinstance(token, str) and token for token in raw_tokens):
            raise FeedError(f"{path} contains an invalid member token")
        tokens.update(raw_tokens)
    return tokens


def load_catalog(repo: GitRepository, revision: str) -> dict[str, dict[str, Any]]:
    return merge_catalog_by_path(load_catalog_by_path(repo, revision))


def membership_tokens(details: Mapping[str, Any]) -> set[str]:
    return {
        *(f"series:{name}" for name in details["series"]),
        *(f"soc:{name}" for name in details["socs"]),
    }


def new_families(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    *,
    prior_member_tokens: set[str] | None = None,
) -> list[str]:
    """Return new names, excluding renames and wrappers around known hardware."""

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    removed_memberships = [membership_tokens(before[name]) for name in removed]
    known_members = prior_member_tokens or set().union(*removed_memberships)
    return [
        name for name in added if not membership_tokens(after[name]) & known_members
    ]


def read_json_file(path: Path, *, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedError(f"could not read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeedError(f"{path.name} must contain a JSON object")
    return value


def validate_cursor_state(value: Mapping[str, Any]) -> tuple[str | None, str | None]:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise FeedError("cursor state has an unsupported schema_version")
    cursor = value.get("cursor")
    if cursor is not None and (
        not isinstance(cursor, str) or not SHA_RE.fullmatch(cursor)
    ):
        raise FeedError("cursor state contains an invalid commit")
    last_scan = value.get("last_successful_scan")
    if last_scan is not None:
        parse_timestamp(last_scan, context="cursor last_successful_scan")
    return cursor, last_scan


def validate_catalog_shape(value: Any, *, context: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise FeedError(f"{context} must be an object")
    result: dict[str, dict[str, Any]] = {}
    for family_name in sorted(value):
        _name(family_name, context=f"{context} family name")
        details = _mapping(value[family_name], context=f"{context}.{family_name}")
        series = details.get("series")
        socs = details.get("socs")
        sources = details.get("source_files")
        if not all(
            isinstance(item, str) and item
            for item in _sequence(series, context="series")
        ):
            raise FeedError(f"{context}.{family_name}.series contains an invalid name")
        if not all(
            isinstance(item, str) and item for item in _sequence(socs, context="socs")
        ):
            raise FeedError(f"{context}.{family_name}.socs contains an invalid name")
        if not all(
            isinstance(item, str) and item
            for item in _sequence(sources, context="source_files")
        ):
            raise FeedError(
                f"{context}.{family_name}.source_files contains an invalid path"
            )
        canonical = {
            "series": sorted(series),
            "socs": sorted(socs),
            "source_files": sorted(sources),
        }
        if canonical["series"] != series or len(series) != len(set(series)):
            raise FeedError(f"{context}.{family_name}.series is not canonical")
        if canonical["socs"] != socs or len(socs) != len(set(socs)):
            raise FeedError(f"{context}.{family_name}.socs is not canonical")
        if canonical["source_files"] != sources or len(sources) != len(set(sources)):
            raise FeedError(f"{context}.{family_name}.source_files is not canonical")
        result[family_name] = canonical
    return result


def validate_catalog_state(
    value: Mapping[str, Any],
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise FeedError("catalog state has an unsupported schema_version")
    revision = value.get("revision")
    if revision is not None and (
        not isinstance(revision, str) or not SHA_RE.fullmatch(revision)
    ):
        raise FeedError("catalog state contains an invalid revision")
    families = validate_catalog_shape(value.get("families"), context="catalog families")
    if revision is None and families:
        raise FeedError("uninitialized catalog state must not contain families")
    if revision is not None and not families:
        raise FeedError("initialized catalog state must contain families")
    return revision, families


ITEM_KEYS = {
    "id",
    "title",
    "link",
    "published",
    "summary",
    "family",
    "vendor",
    "series",
    "socs",
    "source_files",
    "commit",
    "commit_subject",
}


def validate_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise FeedError("item history must be a list")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_families: set[str] = set()
    for index, raw in enumerate(items):
        item = _mapping(raw, context=f"items[{index}]")
        missing = sorted(ITEM_KEYS - set(item))
        if missing:
            raise FeedError(f"items[{index}] is missing: {', '.join(missing)}")
        if not all(
            isinstance(item[key], str) and item[key]
            for key in (
                "id",
                "title",
                "link",
                "published",
                "summary",
                "family",
                "vendor",
                "commit",
                "commit_subject",
            )
        ):
            raise FeedError(f"items[{index}] contains an invalid string field")
        if not SHA_RE.fullmatch(item["commit"]):
            raise FeedError(f"items[{index}] contains an invalid commit")
        parse_timestamp(item["published"], context=f"items[{index}].published")
        require_http_url(item["link"], key=f"items[{index}].link")
        for key in ("series", "socs", "source_files"):
            values = _sequence(item[key], context=f"items[{index}].{key}")
            if not all(isinstance(value, str) and value for value in values):
                raise FeedError(f"items[{index}].{key} contains an invalid value")
        if item["id"] in seen_ids:
            raise FeedError(f"item history repeats id {item['id']!r}")
        if item["family"] in seen_families:
            raise FeedError(f"item history repeats family {item['family']!r}")
        seen_ids.add(item["id"])
        seen_families.add(item["family"])
        result.append(dict(item))
    expected = sort_items(result)
    if expected != result:
        raise FeedError("item history is not sorted newest-first")
    return result


def load_history(path: Path) -> tuple[list[dict[str, Any]], dt.datetime | None]:
    state = read_json_file(
        path,
        default={"schema_version": SCHEMA_VERSION, "generated_at": None, "items": []},
    )
    if state.get("schema_version") != SCHEMA_VERSION:
        raise FeedError("item history has an unsupported schema_version")
    generated_at = state.get("generated_at")
    parsed_generated_at = None
    if generated_at is not None:
        parsed_generated_at = parse_timestamp(
            generated_at, context="history generated_at"
        )
    return validate_items(state.get("items")), parsed_generated_at


def sort_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item["published"], item["id"]), reverse=True)


def vendor_from_sources(paths: Sequence[str]) -> str:
    vendors = sorted(
        {
            PurePosixPath(path).parts[1]
            for path in paths
            if len(PurePosixPath(path).parts) > 1
        }
    )
    if not vendors:
        raise FeedError("could not derive a vendor from soc.yml source paths")
    return ", ".join(vendors)


def require_xml_text(value: Any, *, context: str) -> str:
    if not isinstance(value, str):
        raise FeedError(f"{context} must be text")
    for character in value:
        codepoint = ord(character)
        if not (
            codepoint in {0x09, 0x0A, 0x0D}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            raise FeedError(f"{context} contains a character forbidden by XML 1.0")
    return value


def describe_names(label: str, names: Sequence[str], *, limit: int = 12) -> str:
    """Render a deterministic, bounded evidence list for an RSS description."""

    shown = list(names[:limit])
    if not shown:
        return f"{label}: none listed."
    rendered = ", ".join(shown)
    remaining = len(names) - len(shown)
    if remaining:
        rendered += f", and {remaining} more"
    return f"{label}: {rendered}."


def make_item(
    *,
    family: str,
    details: Mapping[str, Any],
    commit: str,
    metadata: Mapping[str, str],
    repository_url: str,
) -> dict[str, Any]:
    series = list(details["series"])
    socs = list(details["socs"])
    source_files = list(details["source_files"])
    vendor = vendor_from_sources(source_files)
    link = f"{repository_url}/commit/{commit}"
    summary = " ".join(
        (
            f"Zephyr added the {family} SoC family.",
            f"Vendor: {vendor}.",
            describe_names("Series", series),
            describe_names("SoCs", socs),
            f"Introducing commit: {metadata['commit_subject']}.",
        )
    )
    return {
        "id": f"zephyr-soc-family:{family}:{commit}",
        "title": f"New Zephyr SoC family: {family}",
        "link": link,
        "published": metadata["published"],
        "summary": summary,
        "family": family,
        "vendor": vendor,
        "series": series,
        "socs": socs,
        "source_files": source_files,
        "commit": commit,
        "commit_subject": metadata["commit_subject"],
    }


def rss_bytes(
    config: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    generated_at: dt.datetime,
) -> bytes:
    for key in ("feed_title", "feed_link", "feed_description", "repository_url"):
        require_xml_text(config[key], context=f"config {key}")
    for index, item in enumerate(items[: config["max_feed_items"]]):
        for key in ("title", "link", "id", "summary", "family"):
            require_xml_text(item[key], context=f"RSS item {index} {key}")
    ET.register_namespace("atom", ATOM_NS)
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = config["feed_title"]
    ET.SubElement(channel, "link").text = config["feed_link"]
    ET.SubElement(channel, "description").text = config["feed_description"]
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(
        generated_at
    )
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {"href": config["feed_link"], "rel": "self", "type": "application/rss+xml"},
    )
    for item in items[: config["max_feed_items"]]:
        element = ET.SubElement(channel, "item")
        ET.SubElement(element, "title").text = item["title"]
        ET.SubElement(element, "link").text = item["link"]
        ET.SubElement(element, "guid", {"isPermaLink": "false"}).text = item["id"]
        published = parse_timestamp(
            item["published"], context=f"item {item['id']} published"
        )
        ET.SubElement(element, "pubDate").text = email.utils.format_datetime(published)
        ET.SubElement(element, "description").text = item["summary"]
        ET.SubElement(element, "category").text = item["family"]
        ET.SubElement(
            element, "source", {"url": config["repository_url"]}
        ).text = "Zephyr Project"
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def atomic_write_many(files: Sequence[tuple[Path, bytes]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, content in files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            staged.append((temporary, destination))

        for temporary, destination in staged:
            os.replace(temporary, destination)
        for directory in sorted(
            {destination.parent for destination, _ in files}, key=str
        ):
            try:
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                # Some platforms/filesystems do not support fsync on directories.
                pass
    except OSError as exc:
        raise FeedError(f"could not atomically write generated files: {exc}") from exc
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def scan(
    config: Mapping[str, Any], *, now: dt.datetime | None = None
) -> dict[str, Any]:
    generated_at = (now or utc_now()).astimezone(dt.timezone.utc)
    repo = GitRepository(Path(config["repo"]))
    target = repo.resolve_commit(config["revision"])

    cursor_path = Path(config["cursor_file"])
    catalog_path = Path(config["catalog_file"])
    history_path = Path(config["history_file"])
    feed_path = Path(config["feed_file"])

    cursor_state = read_json_file(
        cursor_path,
        default={
            "schema_version": SCHEMA_VERSION,
            "cursor": None,
            "last_successful_scan": None,
        },
    )
    catalog_state = read_json_file(
        catalog_path,
        default={"schema_version": SCHEMA_VERSION, "revision": None, "families": {}},
    )
    cursor, last_successful_scan = validate_cursor_state(cursor_state)
    catalog_revision, stored_catalog = validate_catalog_state(catalog_state)
    history, history_generated_at = load_history(history_path)

    if (cursor is None) != (catalog_revision is None):
        raise FeedError("cursor and catalog initialization state do not match")
    if cursor is not None and history_generated_at is None:
        raise FeedError("initialized state requires a history generated_at timestamp")
    if cursor is None:
        cutoff = generated_at - dt.timedelta(days=config["backfill_days"])
        recent_commits = repo.first_parent_commits(target, since=cutoff)
        if recent_commits:
            baseline = repo.first_parent(recent_commits[0])
            repo.ensure_first_parent_ancestor(baseline, target)
            catalog_by_path = load_catalog_by_path(repo, baseline)
        else:
            baseline = target
            catalog_by_path = load_catalog_by_path(repo, target)
    else:
        if catalog_revision != cursor:
            raise FeedError("catalog revision does not match cursor")
        resolved_cursor = repo.resolve_commit(cursor)
        if resolved_cursor != cursor:
            raise FeedError("stored cursor does not resolve exactly")
        catalog_by_path = load_catalog_by_path(repo, cursor)
        actual_cursor_catalog = merge_catalog_by_path(catalog_by_path)
        if actual_cursor_catalog != stored_catalog:
            raise FeedError("stored catalog does not match the cursor tree")
        repo.ensure_first_parent_ancestor(cursor, target)
        baseline = cursor

    previous_catalog = merge_catalog_by_path(catalog_by_path)
    if baseline == target:
        commits = []
    else:
        # History simplification happens inside Git, so an annual backfill does
        # not spawn a process for every unrelated Zephyr commit.
        commits = repo.first_parent_commits(
            f"{baseline}..{target}",
            pathspecs=("soc/soc.yml", ":(glob)soc/**/soc.yml"),
        )

    candidates: list[dict[str, Any]] = []
    seen_families = {item["family"] for item in history}
    for commit in commits:
        prior_members = all_member_tokens(catalog_by_path)
        parent = repo.first_parent(commit)
        changes = repo.changed_soc_yml(parent, commit)
        for path, status in changes.items():
            exists = path in catalog_by_path
            if status == "A":
                if exists:
                    raise FeedError(
                        f"commit {commit} adds an existing metadata path {path}"
                    )
                catalog_by_path[path] = parse_soc_yml(
                    repo.read_blob(commit, path), path=path
                )
            elif status == "D":
                if not exists:
                    raise FeedError(
                        f"commit {commit} deletes an unknown metadata path {path}"
                    )
                del catalog_by_path[path]
            else:
                if not exists:
                    raise FeedError(
                        f"commit {commit} modifies an unknown metadata path {path}"
                    )
                catalog_by_path[path] = parse_soc_yml(
                    repo.read_blob(commit, path), path=path
                )

        current_catalog = merge_catalog_by_path(catalog_by_path)
        additions = new_families(
            previous_catalog,
            current_catalog,
            prior_member_tokens=prior_members,
        )
        if additions:
            metadata = repo.commit_metadata(commit)
            for family in additions:
                if family not in seen_families:
                    candidates.append(
                        make_item(
                            family=family,
                            details=current_catalog[family],
                            commit=commit,
                            metadata=metadata,
                            repository_url=config["repository_url"],
                        )
                    )
                    seen_families.add(family)
        previous_catalog = current_catalog

    # A full final read is an intentional fail-closed checksum of the efficient
    # incremental path updates above.  It costs one read per metadata file per
    # run, rather than one read per file per event commit.
    final_by_path = load_catalog_by_path(repo, target)
    if catalog_by_path != final_by_path:
        raise FeedError("incremental metadata state does not match the target tree")
    final_catalog = merge_catalog_by_path(final_by_path)
    if previous_catalog != final_catalog:
        raise FeedError("final catalog does not match the scanned commit history")

    if cursor is not None and not commits:
        # Keep generated files byte-stable when upstream changed only outside
        # soc.yml (or did not change at all).  The older cursor is intentional:
        # a future range scan still covers every subsequent relevant commit.
        build_time = history_generated_at
        if (
            build_time is None and last_successful_scan is not None
        ):  # defensive; validated above
            build_time = parse_timestamp(
                last_successful_scan, context="cursor last_successful_scan"
            )
        if build_time is None:  # pragma: no cover - protected by state validation
            raise FeedError("could not determine the existing feed build time")
        expected_feed = rss_bytes(config, history, build_time)
        try:
            existing_feed = feed_path.read_bytes() if feed_path.exists() else None
        except OSError as exc:
            raise FeedError(f"could not read {feed_path.name}: {exc}") from exc
        if existing_feed != expected_feed:
            # feed.xml is normally a generated, untracked Pages artifact.  A
            # fresh CI checkout must recreate it even when upstream is a no-op.
            atomic_write_many([(feed_path, expected_feed)])
        return {
            "cursor": cursor,
            "commits_scanned": 0,
            "new_items": 0,
            "history_items": len(history),
            "feed_items": min(len(history), config["max_feed_items"]),
        }

    # Do not backfill a family that was added and removed in the same scan range.
    candidates = [item for item in candidates if item["family"] in final_catalog]
    all_items = sort_items([*history, *candidates])
    validate_items(all_items)
    timestamp = isoformat(generated_at)
    history_output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp,
        "items": all_items,
    }
    state_cursor = commits[-1] if commits else target
    catalog_output = {
        "schema_version": SCHEMA_VERSION,
        "revision": state_cursor,
        "families": final_catalog,
    }
    cursor_output = {
        "schema_version": SCHEMA_VERSION,
        "cursor": state_cursor,
        "last_successful_scan": timestamp,
    }

    # Cursor is replaced last.  A failed run therefore never claims an
    # unprocessed commit; the next run also verifies cursor/catalog agreement.
    atomic_write_many(
        [
            (history_path, json_bytes(history_output)),
            (feed_path, rss_bytes(config, all_items, generated_at)),
            (catalog_path, json_bytes(catalog_output)),
            (cursor_path, json_bytes(cursor_output)),
        ]
    )
    return {
        "cursor": state_cursor,
        "commits_scanned": len(commits),
        "new_items": len(candidates),
        "history_items": len(all_items),
        "feed_items": min(len(all_items), config["max_feed_items"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an RSS feed for newly introduced Zephyr SoC families."
    )
    parser.add_argument("--config", required=True, help="JSON configuration file")
    parser.add_argument("--repo", help="override the local Zephyr checkout path")
    parser.add_argument(
        "--revision", help="override the target revision (default: config/HEAD)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(Path(args.config), args.repo, args.revision)
        result = scan(config)
    except (FeedError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
