#!/usr/bin/env python3
"""Report live local model containers without exposing commands or env."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


REPO = Path("/home/divot/git/spark-vllm-docker")
SESSION = "recipes"
RECIPE_ROOTS = (REPO / "local-recipes", REPO / "recipes")


@dataclass
class Container:
    name: str
    image: str
    status: str
    ports: str
    model: str | None = None
    served_name: str | None = None
    container_port: int = 8000
    host_port: int = 8000
    api_models: tuple[str, ...] = ()
    api_state: str = "unreachable"


def run(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def docker_containers() -> list[Container]:
    result = run(
        [
            "docker",
            "ps",
            "--format",
            "{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else "docker ps failed"
        raise RuntimeError(message)

    containers: list[Container] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) == 4:
            containers.append(Container(*fields))
    return containers


def option(tokens: list[str], *names: str) -> str | None:
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return None


def process_metadata(container: Container) -> None:
    result = run(["docker", "top", container.name, "-eo", "args"])
    if result.returncode != 0:
        return

    best: tuple[int, str | None, str | None, int] | None = None
    for line in result.stdout.splitlines()[1:]:
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue

        model: str | None = None
        score = 0
        for index in range(len(tokens) - 2):
            if Path(tokens[index]).name == "vllm" and tokens[index + 1] == "serve":
                model = tokens[index + 2]
                score = 3
                break

        if model is None and any(Path(token).name == "llama-server" for token in tokens):
            model = option(tokens, "-hf", "--hf-repo", "--model")
            score = 3 if model else 1

        module = option(tokens, "-m")
        if model is None and module and "vllm" in module:
            model = option(tokens, "--model")
            score = 2 if model else 1

        if model is None:
            explicit_model = option(tokens, "--model")
            if explicit_model and any("vllm" in token.lower() for token in tokens):
                model = explicit_model
                score = 2

        if score == 0:
            continue

        port_text = option(tokens, "--port") or "8000"
        try:
            port = int(port_text)
        except ValueError:
            port = 8000
        served = option(tokens, "--served-model-name", "--alias")
        candidate = (score, model, served, port)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best:
        _, container.model, container.served_name, container.container_port = best
        container.host_port = mapped_host_port(container.ports, container.container_port)


def mapped_host_port(ports: str, container_port: int) -> int:
    pattern = re.compile(r"(?:[\d.]+|\[::\]):(\d+)->(\d+)/(?:tcp|udp)")
    for host, inner in pattern.findall(ports):
        if int(inner) == container_port:
            return int(host)
    return container_port


def query_api(container: Container) -> None:
    url = f"http://127.0.0.1:{container.host_port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            payload = json.load(response)
        ids = []
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            for entry in payload["data"]:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    ids.append(entry["id"])
        container.api_models = tuple(dict.fromkeys(ids))
        container.api_state = "ready" if ids else "responding (no model IDs)"
    except urllib.error.HTTPError as exc:
        container.api_state = f"HTTP {exc.code}"
    except (OSError, ValueError, json.JSONDecodeError):
        container.api_state = "starting/unreachable"


def recipe_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    paths = (path for root in RECIPE_ROOTS for path in root.rglob("*.yaml"))
    for path in sorted(paths):
        try:
            payload = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        relative = str(path.relative_to(REPO))
        values = {path.stem}
        for key in ("model", "name"):
            value = payload.get(key)
            if isinstance(value, str):
                values.add(value)
        command = payload.get("command")
        if isinstance(command, str):
            try:
                tokens = shlex.split(command.replace("\\\n", " "))
            except ValueError:
                tokens = []
            for i in range(len(tokens) - 2):
                if Path(tokens[i]).name == "vllm" and tokens[i + 1] == "serve":
                    values.add(tokens[i + 2])
                    break
            if any(Path(token).name == "llama-server" for token in tokens):
                target = option(tokens, "-hf", "--hf-repo", "--model")
                if target:
                    values.add(target)
        for value in values:
            index.setdefault(value, set()).add(relative)
    return index


def runner_windows() -> str:
    result = run(["screen", "-S", SESSION, "-Q", "windows"])
    if result.returncode != 0:
        return "unavailable"
    windows = re.findall(r"\d+[^\s]*\s+([^\s]+)", result.stdout.strip())
    selected = [name for name in windows if name == "control" or name.startswith("runner-")]
    return ", ".join(selected) if selected else "none"


def likely_model_server(container: Container) -> bool:
    text = f"{container.name} {container.image}".lower()
    return bool(
        container.model
        or container.served_name
        or "vllm" in text
        or "llama" in text
    )


def matches(container: Container, index: dict[str, set[str]]) -> list[str]:
    keys = list(container.api_models)
    keys.extend(value for value in (container.model, container.served_name) if value)
    found: set[str] = set()
    for key in keys:
        found.update(index.get(key, set()))
        found.update(index.get(Path(key).name, set()))
    conventional = container.name.removeprefix("vllm-")
    found.update(index.get(conventional, set()))
    return sorted(found)


def main() -> int:
    try:
        containers = docker_containers()
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Inspection failed: {exc}", file=sys.stderr)
        return 1

    for container in containers:
        process_metadata(container)
    candidates = [
        container for container in containers if likely_model_server(container)
    ]
    for container in candidates:
        query_api(container)

    print(f"screen windows: {runner_windows()}")
    if not candidates:
        print("No running model containers found.")
        return 0

    index = recipe_index()
    print(f"Running model containers: {len(candidates)}")
    for container in candidates:
        api_models = ", ".join(container.api_models)
        model = api_models or container.served_name or container.model or "unknown/loading"
        recipes = matches(container, index)
        sources = sorted(
            {
                "local" if recipe.startswith("local-recipes/") else "upstream"
                for recipe in recipes
            }
        )
        print(f"- container: {container.name}")
        print(f"  model: {model}")
        print(f"  source: {', '.join(sources) if sources else 'unknown'}")
        print(f"  recipe: {', '.join(recipes) if recipes else 'unknown'}")
        print(f"  port: {container.host_port}")
        print(f"  readiness: {container.api_state}")
        print(f"  image: {container.image}")
        print(f"  container status: {container.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
