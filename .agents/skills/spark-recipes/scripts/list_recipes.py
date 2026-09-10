#!/usr/bin/env python3
"""List local and upstream recipe definitions as separate inventories."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

import yaml


REPO = Path("/home/divot/git/spark-vllm-docker")
SOURCES = (
    ("local", REPO / "local-recipes"),
    ("upstream", REPO / "recipes"),
)


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


def topology(data: dict[str, Any], path: Path, source: str) -> str:
    if data.get("cluster_only"):
        return "cluster-only"
    if data.get("solo_only"):
        return "solo-only"
    if source == "upstream" and path.parent.name.endswith("-spark-cluster"):
        return "cluster-only"
    return "solo-capable"


def list_source(source: str, root: Path) -> None:
    paths = sorted((*root.rglob("*.yaml"), *root.rglob("*.yml")))
    print(f"{source.title()} recipes ({root.relative_to(REPO)}/): {len(paths)}")
    if not paths:
        print("  none")
        return
    for path in paths:
        relative = path.relative_to(REPO)
        try:
            payload = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"- {relative} [invalid: {exc.__class__.__name__}]")
            continue
        if not isinstance(payload, dict):
            print(f"- {relative} [invalid: expected mapping]")
            continue
        name = payload.get("name") or path.stem
        model = payload.get("model") or command_model(payload.get("command")) or "unspecified"
        print(f"- {relative} | {name} | {topology(payload, path, source)}")
        print(f"  model: {model}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--local", action="store_true", help="list only local recipes")
    group.add_argument(
        "--upstream", action="store_true", help="list only upstream recipes"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = SOURCES
    if args.local:
        selected = (SOURCES[0],)
    elif args.upstream:
        selected = (SOURCES[1],)
    for index, (source, root) in enumerate(selected):
        if index:
            print()
        list_source(source, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
