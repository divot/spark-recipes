#!/usr/bin/env python3
"""Maintain the versioned JSON history for Spark model recipes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml


REPO = Path("/home/divot/git/spark-vllm-docker")
DEFAULT_LOG = REPO / "recipe-history.json"
RECIPE_ROOTS = (REPO / "local-recipes", REPO / "recipes")
SENSITIVE_KEY = re.compile(r"(?:token|password|secret|api[_-]?key|credential)", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_time(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone or end in Z")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_git_revision(revision: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Git revision not found: {revision}")
    return result.stdout.strip()


def recipe_dirty(relative: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", relative],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return bool(result.stdout.strip())


def command_model(command: Any) -> str | None:
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command.replace("\\\n", " "))
    except ValueError:
        return None
    for index in range(len(tokens) - 2):
        if Path(tokens[index]).name == "vllm" and tokens[index + 1] == "serve":
            return tokens[index + 2]
    if any(Path(token).name == "llama-server" for token in tokens):
        for index, token in enumerate(tokens):
            if token in {"-hf", "--hf-repo", "--model"} and index + 1 < len(tokens):
                return tokens[index + 1]
            for name in ("-hf", "--hf-repo", "--model"):
                if token.startswith(name + "="):
                    return token.split("=", 1)[1]
    return None


def recipe_files() -> list[Path]:
    return sorted(
        path
        for root in RECIPE_ROOTS
        for pattern in ("*.yaml", "*.yml")
        for path in root.rglob(pattern)
    )


def recipe_metadata(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"recipe is not a YAML mapping: {path}")
    relative = path.relative_to(REPO).as_posix()
    source = "local" if relative.startswith("local-recipes/") else "upstream"
    return {
        "source": source,
        "recipe_path": relative,
        "recipe_name": str(payload.get("name") or path.stem),
        "model": payload.get("model") or command_model(payload.get("command")),
        "present": True,
        "recipe_sha256": sha256(path),
    }


def git_recipe_metadata(revision: str, relative: str) -> tuple[dict[str, Any], str]:
    normalized = relative.removeprefix("./")
    path = Path(normalized)
    if path.is_absolute() or path.suffix not in {".yaml", ".yml"}:
        raise ValueError(f"invalid recipe path: {relative}")
    if ".." in path.parts:
        raise ValueError(f"recipe path may not contain '..': {relative}")
    if not path.parts or path.parts[0] not in {"local-recipes", "recipes"}:
        raise ValueError("Git recipe path must be below local-recipes/ or recipes/")
    resolved_revision = resolve_git_revision(revision)
    result = subprocess.run(
        ["git", "show", f"{resolved_revision}:{normalized}"],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"recipe not found at {revision}:{normalized}")
    payload = yaml.safe_load(result.stdout) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"recipe is not a YAML mapping: {revision}:{normalized}")
    current_path = REPO / normalized
    metadata = {
        "source": "local" if path.parts[0] == "local-recipes" else "upstream",
        "recipe_path": normalized,
        "recipe_name": str(payload.get("name") or path.stem),
        "model": payload.get("model") or command_model(payload.get("command")),
        "present": current_path.exists(),
        "recipe_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
    }
    return metadata, resolved_revision


def empty_log() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "time_zone": "UTC",
        "updated_at": None,
        "recipes": {},
    }


def load_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_log()
    with path.open() as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"unsupported or invalid recipe history: {path}")
    if not isinstance(data.get("recipes"), dict):
        raise ValueError(f"recipe history has no recipes mapping: {path}")
    return data


def write_log(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def lock_path(log_path: Path) -> Path:
    key = hashlib.sha256(str(log_path.resolve()).encode()).hexdigest()[:16]
    return Path("/tmp") / f"spark-recipe-history-{key}.lock"


@contextmanager
def mutate_log(path: Path) -> Iterator[dict[str, Any]]:
    lock = lock_path(path)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        data = load_log(path)
        try:
            yield data
        except Exception:
            raise
        else:
            data["updated_at"] = utc_now()
            data["recipes"] = dict(sorted(data["recipes"].items()))
            write_log(path, data)
    finally:
        os.close(descriptor)


def new_entry(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        **metadata,
        "attempted": False,
        "ever_started_successfully": False,
        "last_attempt_started_at": None,
        "last_successful_start_at": None,
        "last_stop_at": None,
        "attempts": [],
        "notes": [],
        "recipe_changes": [],
    }


def refresh_entry(
    data: dict[str, Any], path: Path, now: str, description: str | None = None
) -> dict[str, Any]:
    metadata = recipe_metadata(path)
    key = metadata["recipe_path"]
    entry = data["recipes"].get(key)
    if entry is None:
        entry = new_entry(metadata)
        data["recipes"][key] = entry
        if description:
            entry["recipe_changes"].append(
                {
                    "recorded_at": now,
                    "description": description,
                    "previous_recipe_sha256": None,
                    "recipe_sha256": metadata["recipe_sha256"],
                    "git_revision": git_revision(),
                }
            )
        return entry

    previous_hash = entry.get("recipe_sha256")
    current_hash = metadata["recipe_sha256"]
    if previous_hash != current_hash:
        entry.setdefault("recipe_changes", []).append(
            {
                "recorded_at": now,
                "description": description
                or "Recipe content changed; no description was supplied before synchronization.",
                "previous_recipe_sha256": previous_hash,
                "recipe_sha256": current_hash,
                "git_revision": git_revision(),
            }
        )
    elif description:
        entry.setdefault("recipe_changes", []).append(
            {
                "recorded_at": now,
                "description": description,
                "previous_recipe_sha256": previous_hash,
                "recipe_sha256": current_hash,
                "git_revision": git_revision(),
            }
        )
    entry.update(metadata)
    return entry


def current_candidates() -> list[tuple[Path, dict[str, Any]]]:
    candidates = []
    for path in recipe_files():
        try:
            candidates.append((path, recipe_metadata(path)))
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return candidates


def resolve_recipe(selector: str, data: dict[str, Any]) -> tuple[str, Path | None]:
    normalized = selector.removeprefix("./")
    if normalized in data["recipes"]:
        candidate = REPO / normalized
        return normalized, candidate if candidate.exists() else None

    target = selector.casefold()
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path, metadata in current_candidates():
        values = {
            metadata["recipe_path"].casefold(),
            path.stem.casefold(),
            str(metadata["recipe_name"]).casefold(),
        }
        if metadata.get("model"):
            values.add(str(metadata["model"]).casefold())
        if target in values:
            matches.append((path, metadata))
    if not matches:
        raise ValueError(f"recipe not found: {selector}")
    if len(matches) > 1:
        options = ", ".join(metadata["recipe_path"] for _, metadata in matches)
        raise ValueError(f"recipe selector is ambiguous: {selector} ({options})")
    path, metadata = matches[0]
    return metadata["recipe_path"], path


def require_present_recipe(
    selector: str, data: dict[str, Any], now: str, description: str | None = None
) -> tuple[str, dict[str, Any]]:
    key, path = resolve_recipe(selector, data)
    if path is None:
        raise ValueError(f"recipe is no longer present: {key}")
    return key, refresh_entry(data, path, now, description)


def parse_parameters(value: str) -> dict[str, Any]:
    try:
        parameters = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON object: {exc}") from exc
    if not isinstance(parameters, dict):
        raise argparse.ArgumentTypeError("parameters must be a JSON object")

    def check(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if SENSITIVE_KEY.search(str(key)):
                    raise argparse.ArgumentTypeError(
                        f"refusing sensitive parameter key: {key}"
                    )
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(parameters)
    return parameters


def attempt_id(entry: dict[str, Any]) -> str:
    return f"attempt-{len(entry.setdefault('attempts', [])) + 1:04d}"


def recompute_summary(entry: dict[str, Any]) -> None:
    attempts = entry.setdefault("attempts", [])
    entry["attempted"] = bool(attempts)
    entry["ever_started_successfully"] = any(
        bool(attempt.get("successful_start")) for attempt in attempts
    )
    starts = [attempt.get("started_at") for attempt in attempts if attempt.get("started_at")]
    successes = [
        attempt.get("ready_at")
        for attempt in attempts
        if attempt.get("successful_start") and attempt.get("ready_at")
    ]
    stops = [attempt.get("stopped_at") for attempt in attempts if attempt.get("stopped_at")]
    entry["last_attempt_started_at"] = max(starts, default=None)
    entry["last_successful_start_at"] = max(successes, default=None)
    entry["last_stop_at"] = max(stops, default=None)


def require_active_status(attempt: dict[str, Any]) -> None:
    if attempt.get("status") not in {"starting", "running"}:
        raise ValueError(
            f"attempt {attempt.get('id')} is not active (status={attempt.get('status')})"
        )


def find_attempt(
    entry: dict[str, Any], requested: str | None, active_only: bool = False
) -> dict[str, Any]:
    attempts = entry.setdefault("attempts", [])
    if requested:
        for attempt in attempts:
            if attempt.get("id") == requested:
                return attempt
        raise ValueError(f"attempt not found: {requested}")
    allowed = {"starting", "running"} if active_only else None
    for attempt in reversed(attempts):
        if allowed is None or attempt.get("status") in allowed:
            return attempt
    qualifier = "active " if active_only else ""
    raise ValueError(f"no {qualifier}attempt exists for this recipe")


def attempt_record(
    entry: dict[str, Any],
    now: str,
    started_at: str | None,
    parameters: dict[str, Any],
    *,
    started_at_is_approximate: bool = False,
    revision: str | None = None,
    dirty: bool | None = None,
) -> dict[str, Any]:
    record = {
        "id": attempt_id(entry),
        "recorded_at": now,
        "started_at": started_at,
        "started_at_is_approximate": started_at_is_approximate,
        "ready_at": None,
        "stopped_at": None,
        "ended_at": None,
        "status": "starting",
        "successful_start": False,
        "parameters": parameters,
        "recipe_sha256": entry.get("recipe_sha256"),
        "git_revision": revision or git_revision(),
        "recipe_dirty": recipe_dirty(entry["recipe_path"]) if dirty is None else dirty,
        "failure_reason": None,
        "stop_reason": None,
        "stop_time_is_observation": False,
        "notes": [],
        "metrics": [],
    }
    entry["attempts"].append(record)
    recompute_summary(entry)
    return record


def add_note(target: list[dict[str, Any]], now: str, text: str) -> None:
    target.append({"recorded_at": now, "text": text})


def command_sync(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        seen = set()
        for path in recipe_files():
            entry = refresh_entry(data, path, now)
            seen.add(entry["recipe_path"])
        for key, entry in data["recipes"].items():
            if key not in seen:
                entry["present"] = False
    print(f"Synchronized {len(seen)} recipes into {args.log}")


def command_register_git(args: argparse.Namespace) -> None:
    now = utc_now()
    metadata, revision = git_recipe_metadata(args.revision, args.recipe_path)
    key = metadata["recipe_path"]
    with mutate_log(args.log) as data:
        existing = data["recipes"].get(key)
        if existing is None:
            entry = new_entry(metadata)
            data["recipes"][key] = entry
        else:
            previous_hash = existing.get("recipe_sha256")
            if previous_hash != metadata["recipe_sha256"]:
                existing.setdefault("recipe_changes", []).append(
                    {
                        "recorded_at": now,
                        "description": f"Registered recipe content from Git revision {revision}.",
                        "previous_recipe_sha256": previous_hash,
                        "recipe_sha256": metadata["recipe_sha256"],
                        "git_revision": revision,
                    }
                )
            existing.update(metadata)
            entry = existing
        if args.note:
            add_note(entry["notes"], now, args.note)
    print(f"Registered {key} from {revision}")


def command_begin(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now)
        try:
            find_attempt(entry, None, active_only=True)
        except ValueError:
            pass
        else:
            raise ValueError(f"an active attempt already exists for {key}")
        record = attempt_record(entry, now, now, args.parameters_json)
        if args.note:
            add_note(record["notes"], now, args.note)
    print(f"Recorded {record['id']} for {key} at {now}")


def command_ready(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now)
        attempt = find_attempt(entry, args.attempt, active_only=not bool(args.attempt))
        require_active_status(attempt)
        attempt["status"] = "running"
        attempt["successful_start"] = True
        attempt["ready_at"] = now
        recompute_summary(entry)
    print(f"Marked {key} {attempt['id']} ready at {now}")


def command_fail(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now)
        attempt = find_attempt(entry, args.attempt, active_only=not bool(args.attempt))
        require_active_status(attempt)
        attempt["status"] = "failed"
        attempt["failure_reason"] = args.reason
        attempt["ended_at"] = now
        recompute_summary(entry)
    print(f"Marked {key} {attempt['id']} failed at {now}")


def command_stop(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now)
        attempt = find_attempt(entry, args.attempt, active_only=not bool(args.attempt))
        require_active_status(attempt)
        attempt["status"] = "stopped-externally" if args.observed else "stopped"
        attempt["stopped_at"] = now
        attempt["ended_at"] = now
        attempt["stop_reason"] = args.reason
        attempt["stop_time_is_observation"] = bool(args.observed)
        recompute_summary(entry)
    print(f"Marked {key} {attempt['id']} stopped at {now}")


def command_record_failure(args: argparse.Namespace) -> None:
    now = utc_now()
    started = normalize_time(args.started_at)
    ended = normalize_time(args.ended_at)
    with mutate_log(args.log) as data:
        key, path = resolve_recipe(args.recipe, data)
        entry = (
            refresh_entry(data, path, now)
            if path is not None
            else data["recipes"][key]
        )
        revision = resolve_git_revision(args.git_revision) if args.git_revision else None
        record = attempt_record(
            entry,
            now,
            started,
            args.parameters_json,
            started_at_is_approximate=args.started_at_approximate,
            revision=revision,
            dirty=False if revision else None,
        )
        record["status"] = "failed"
        record["failure_reason"] = args.reason
        record["ended_at"] = ended
        if args.note:
            add_note(record["notes"], now, args.note)
        recompute_summary(entry)
    print(f"Recorded historical failure {record['id']} for {key}")


def command_note(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, path = resolve_recipe(args.recipe, data)
        entry = (
            refresh_entry(data, path, now)
            if path is not None
            else data["recipes"][key]
        )
        if args.attempt:
            attempt = find_attempt(entry, args.attempt)
            add_note(attempt["notes"], now, args.text)
            destination = attempt["id"]
        else:
            add_note(entry["notes"], now, args.text)
            destination = "recipe"
    print(f"Added note to {key} ({destination})")


def command_metric(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now)
        attempt = find_attempt(entry, args.attempt)
        attempt["metrics"].append(
            {
                "recorded_at": now,
                "tokens_per_second": args.tokens_per_second,
                "query": args.query,
                "parameters": args.parameters_json,
                "notes": args.notes,
            }
        )
    print(f"Added throughput measurement to {key} ({attempt['id']})")


def command_change(args: argparse.Namespace) -> None:
    now = utc_now()
    with mutate_log(args.log) as data:
        key, entry = require_present_recipe(args.recipe, data, now, args.description)
    print(f"Recorded recipe change for {key}: {args.description}")


def command_show(args: argparse.Namespace) -> None:
    data = load_log(args.log)
    if args.recipe:
        key, _ = resolve_recipe(args.recipe, data)
        print(json.dumps(data["recipes"][key], indent=2, ensure_ascii=False))
        return
    entries = sorted(
        data["recipes"].values(),
        key=lambda entry: (entry.get("source") != "local", entry["recipe_path"]),
    )
    print(f"Tracked recipes: {len(entries)}")
    for entry in entries:
        print(
            f"- {entry['recipe_path']} | attempted={str(entry['attempted']).lower()} "
            f"| successful={str(entry['ever_started_successfully']).lower()} "
            f"| attempts={len(entry.get('attempts', []))} "
            f"| notes={len(entry.get('notes', []))}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync", help="synchronize all recipe definitions")

    register = subparsers.add_parser(
        "register-git", help="register a recipe that exists in another Git revision"
    )
    register.add_argument("revision")
    register.add_argument("recipe_path")
    register.add_argument("--note")

    begin = subparsers.add_parser("begin", help="record a live launch attempt")
    begin.add_argument("recipe")
    begin.add_argument("--parameters-json", type=parse_parameters, default={})
    begin.add_argument("--note")

    ready = subparsers.add_parser("ready", help="mark an attempt API-ready")
    ready.add_argument("recipe")
    ready.add_argument("--attempt")

    fail = subparsers.add_parser("fail", help="mark an active attempt failed")
    fail.add_argument("recipe")
    fail.add_argument("--attempt")
    fail.add_argument("--reason", required=True)

    stop = subparsers.add_parser("stop", help="mark an attempt stopped")
    stop.add_argument("recipe")
    stop.add_argument("--attempt")
    stop.add_argument("--reason")
    stop.add_argument(
        "--observed",
        action="store_true",
        help="the exact stop time is unknown; this is the observation time",
    )

    historical = subparsers.add_parser(
        "record-failure", help="record an earlier unsuccessful attempt"
    )
    historical.add_argument("recipe")
    historical.add_argument("--reason", required=True)
    historical.add_argument("--started-at")
    historical.add_argument("--started-at-approximate", action="store_true")
    historical.add_argument("--ended-at")
    historical.add_argument("--git-revision")
    historical.add_argument("--parameters-json", type=parse_parameters, default={})
    historical.add_argument("--note")

    note = subparsers.add_parser("note", help="add a free-form note")
    note.add_argument("recipe")
    note.add_argument("--text", required=True)
    note.add_argument("--attempt")

    metric = subparsers.add_parser("metric", help="record measured throughput")
    metric.add_argument("recipe")
    metric.add_argument("--tokens-per-second", type=float, required=True)
    metric.add_argument("--query", required=True)
    metric.add_argument("--parameters-json", type=parse_parameters, default={})
    metric.add_argument("--notes")
    metric.add_argument("--attempt")

    change = subparsers.add_parser("change", help="describe a recipe revision")
    change.add_argument("recipe")
    change.add_argument("--description", required=True)

    show = subparsers.add_parser("show", help="shw tracking summaries or one record")
    show.add_argument("recipe", nargs="?")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    commands = {
        "sync": command_sync,
        "register-git": command_register_git,
        "begin": command_begin,
        "ready": command_ready,
        "fail": command_fail,
        "stop": command_stop,
        "record-failure": command_record_failure,
        "note": command_note,
        "metric": command_metric,
        "change": command_change,
        "show": command_show,
    }
    try:
        commands[args.command](args)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
