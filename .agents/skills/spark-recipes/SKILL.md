---
name: spark-recipes
description: Operate upstream recipes from recipes/ and custom local recipes from local-recipes/ in /home/divot/git/spark-vllm-docker on this user's single DGX Spark. Use when asked which recipes or models are available, which models are running, or to start or stop a recipe. Keep all operational shell work in the shared GNU screen session named recipes and use the repository .venv. Do not use for repository development.
---

# Spark Recipe Manager

Manage only the local, single-node recipe runtime in
`/home/divot/git/spark-vllm-docker`. Follow that repository's `AGENTS.md` and
operational runbook. Never use cluster discovery, cluster mode, node lists, SSH,
or InfiniBand workflows on this installation. Every recipe launch uses
`--solo`, including recipes that can also run on a cluster. If a selected recipe
is cluster-only, explain that it cannot run on this installation and stop.

## Hard runtime invariants

- Perform every repository, Docker, process, API, and log command inside GNU
  screen session `recipes`. The command needed to create or attach to that
  session is the sole control-plane exception.
- Attach with `screen -x recipes`, not `screen -r`, so the user can attach at
  the same time. If it does not exist, create it with a `control` window, then
  attach shared. Do not replace or quit an existing session.
- Run `source .venv/bin/activate` after entering the repository in every newly
  created shell/window. Do not assume activation carries across windows.
- Run each recipe in its own window named exactly
  `runner-<recipe-name>`, where `<recipe-name>` is the resolved recipe filename
  without `.yaml`. Management-only commands may run in `control`.
- Stay attached while issuing commands or actively monitoring logs. Detach with
  `Ctrl-a d` whenever neither is happening. Detaching must not stop a recipe.
- Treat the screen session as shared mutable state. The user may change it or
  Docker independently; re-inspect live state before every answer or mutation.

Typical session bootstrap, adapted to the available terminal tool:

```bash
TERM=xterm screen -DmS recipes -t control /bin/bash -i  # only if absent
TERM=xterm screen -x recipes
cd /home/divot/git/spark-vllm-docker
source .venv/bin/activate
```

## Inspect live models

Never answer from chat history, an earlier observation, window names alone, or
a saved registry. From inside `recipes`, run:

```bash
python .agents/skills/spark-recipes/scripts/inspect_models.py
```

The inspector reconciles current local Docker containers, sanitized vLLM
process metadata, API readiness, matching recipe YAML, and runner windows. Use
fresh output even if the previous check was recent. If it fails, fall back to
read-only `docker ps`, sanitized `docker top`, screen window inspection, and
`/v1/models` requests; never print complete process commands or environment
variables.

When asked what is running, list every detected model separately with recipe
(when identifiable), container, port, and `ready` or `starting/unreachable`.
Say explicitly when none are running. A live container is not proof of a ready
model; prefer the API response for served model identity and readiness.

## Recipe sources and terminology

Keep these sources distinct in every inventory, resolution, and handoff:

- `recipes/` contains **upstream recipes** maintained by the upstream project.
- `local-recipes/` contains **local recipes** created and developed for this
  installation.

"Local recipe" means only a YAML file below `local-recipes/`; it does not mean
every recipe present in the local Git checkout. When asked for local recipes,
show only `local-recipes/`. When asked for upstream recipes, show only
`recipes/`. When asked generally for available recipes, show both in separately
labeled sections, with local recipes first. Never mix the two into an unlabeled
list or describe upstream recipes as local.

Inventory recipes from inside `recipes` with the deterministic helper:

```bash
python .agents/skills/spark-recipes/scripts/list_recipes.py          # both
python .agents/skills/spark-recipes/scripts/list_recipes.py --local  # local only
python .agents/skills/spark-recipes/scripts/list_recipes.py --upstream
```

An available recipe definition does not prove its image or model artifacts are
prepared. State that distinction when it matters.

## Resolve a recipe

Work from the repository root. Resolve a user name against YAML files below both
recipe roots. An explicit `local-recipes/...` or `recipes/...` path fixes the
source. Otherwise prefer an exact filename stem, then exact recipe `name` or
`model`; ask if multiple candidates remain or the same stem occurs in both
sources. `./run-recipe.sh --list` shows only top-level upstream recipes and is
not a complete inventory.

After resolution, always pass the explicit repository-relative path to
`run-recipe.sh`, including for upstream recipes. This preserves provenance and
prevents its bare-name fallback from silently selecting `recipes/` when a local
recipe was intended.

Inspect the selected YAML, its topology flags, defaults, model, port, image or
build, mods, and any relevant source-specific comments or current changelog
guidance before launching. Do not silently substitute between local and
upstream recipes. Reject `cluster_only: true` here.

Use these deterministic identifiers:

- window: `runner-<recipe-name>`
- container: `vllm-<recipe-name>`

If that container or runner window already represents a live launch of the
same recipe, treat start as an idempotent no-op and report its current state.
Do not reuse a name owned by a different or stale live process.
If local and upstream recipes share the same filename stem, their deterministic
window and container names collide; do not run them concurrently, and require
the user to identify the source before starting either one.

## Start a recipe

Starting authorizes the launch, not image builds, model downloads, forced
refreshes, discovery, or stopping other models.

1. Re-inspect live models and screen windows.
2. Resolve and inspect the exact recipe. Determine its configured solo port.
3. Check current listeners and containers. If the port is occupied by another
   model, ask which port to use; never auto-allocate or stop the occupant.
4. Run the required dry-run inside `recipes`, isolated from local secrets:

   ```bash
   ./run-recipe.sh RECIPE_PATH --config /dev/null --dry-run --solo \
     --name vllm-RECIPE_NAME
   ```

   Review the generated solo command, port, model, image, mods, and material
   overrides. Stop on conflicts or validation errors.
5. Create/select window `runner-RECIPE_NAME`, enter the repository, activate
   `.venv`, and execute the real command there in the foreground:

   ```bash
   exec ./run-recipe.sh RECIPE_PATH --solo --name vllm-RECIPE_NAME
   ```

   Pass only overrides explicitly requested or required to resolve an approved
   port conflict. Do not add `--setup` by default.
6. Monitor that runner window and bounded container logs until `/v1/models`
   reports ready or the launch clearly fails. Use the inspector again for the
   final verification. During a long load, provide concise progress updates.
7. Detach after active monitoring ends, leaving the recipe and session alive.

If launch fails because an image or model artifact is missing, report exactly
which preparation is required and ask before retrying with `--setup`. Once the
user authorizes setup, repeat the dry-run and run the same recipe/window/name
with `--setup`. Never use `--force-build` or `--force-download` unless the user
explicitly requests replacement or refresh.

## Stop a model

Re-inspect first. Resolve the target against live API model IDs, recipe names or
paths, container names, and runner window names. If the user omits a target,
stop it only when exactly one model is live; if several are live or the match is
uncertain, ask one concise clarifying question.

From `control` inside `recipes`, stop only the resolved container through the
repository launcher:

```bash
./launch-cluster.sh --solo --name ACTUAL_CONTAINER_NAME stop
```

Do not use an unqualified default container name. Do not stop unrelated
containers, kill the whole screen session, remove containers/images, or clear
caches. Verify with the live inspector that the selected model is gone and that
other models remain unchanged. A foreground runner window should exit when its
container stops; do not destroy shared windows merely for tidiness.

## Secret and handoff rules

Never run `--show-env`, display `.env`, inspect container environments, echo
tokens, or reproduce full process command lines. Avoid recording expanded
secrets in dry-runs or screen logs; use `--config /dev/null` for solo dry-runs.

After start/stop, report whether the recipe is local or upstream, its path,
model, container, port, readiness, and whether ordinary launch or explicitly
authorized setup was used. Do not expose tokens or unrelated host configuration.
