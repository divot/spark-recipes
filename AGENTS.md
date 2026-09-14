# Agent Instructions

These instructions apply to the entire repository.

## Repository

This project provides Bash and Python orchestration for running vLLM on one or
more NVIDIA DGX Spark systems. Work from the repository root and read `README.md`
for the public project overview.

## Choose One Guide

- **Use or operate the repository:** For host preparation, recipe selection,
  cluster discovery, image or model setup, recipe launches, and live-server
  verification, follow `docs/AGENT_RUNBOOK.md`.
- **Develop the repository:** For inspection, fixes, features, reviews, tests,
  or changes to scripts, recipes, mods, Dockerfiles, and documentation, follow
  `docs/AGENT_DEVELOPMENT.md`.
- **Both:** Follow the development guide first. Use the operational runbook
  afterward only when the user also requested a real build, download, or launch.

Read only the guide relevant to the task unless the work crosses that boundary.
A recipe `--dry-run` used to validate generated commands is development. A
non-dry recipe run, `--setup`, discovery, image preparation, model download, or
container launch is operation.

## Common Boundaries

- Inspect before changing repository, host, container, or cluster state.
- Preserve unrelated user changes and existing local configuration or artifacts.
- Do not expose credentials or `.env` contents in chat, logs, diffs, or commands.
- Operational tasks do not authorize source changes. Development tasks do not
  authorize real deployments. Perform both only when the user requests both.
- Do not prune, overwrite, stop, remove, or force-refresh existing resources
  unless the requested task requires it.

## Recipe Provenance and History

- `recipes/` contains upstream recipe definitions. `local-recipes/` contains
  recipes developed locally; never describe the upstream directory as local.
- Operational attempts, readiness, stops, measurements, free-form notes, launch
  parameters, and recipe-change descriptions belong in `recipe-history.json`.
  Update it with `.agents/skills/spark-recipes/scripts/recipe_log.py`; do not
  store credentials or secret-bearing raw command lines.
- When changing a recipe, record a concise change description before another
  history synchronization can replace the tracked recipe hash.
- Commit repository and recipe-history changes in focused logical increments,
  staging only the intended files and preserving unrelated user work.

## Long-running commands

The primary agent is responsible for planning, implementation decisions,
debugging, and interpreting failures.

When a command enters a long passive phase such as:

- compiling
- downloading
- installing packages
- building containers
- waiting for remote jobs
- waiting for tests that take several minutes

delegate the passive monitoring to a fresh subagent using:

- model: gpt-5.6-luna
- reasoning_effort: low

Before delegating, give the monitoring agent enough context to monitor without
asking the primary agent for help: identify the command or job, its terminal or
Screen window, the expected phases, the success and failure signals, and any
safe progress checks appropriate to the operation. For a model download, this
includes the expected cache or output location and may include watching the
size and modification time of partial files when the download command itself
is quiet. Do not include credentials or secret-bearing raw commands.

In particular, `hf-download.sh` runs `uvx hf download`, which may emit no new
terminal output while transferring a model tens or hundreds of gigabytes in
size. When that phase is expected, tell the monitoring agent explicitly. It can
confirm that the downloader remains alive and inspect aggregate cache growth or
the sizes and mtimes of Hugging Face `.incomplete` files without printing full
process command lines. Lack of `uvx` output alone never establishes a stall.

The monitoring agent must:

- remain assigned until the command succeeds, clearly fails, or requires a
  decision that only the primary agent or user can make;
- wait through long silent phases without treating silence as failure or as a
  reason to return control;
- expect large image layers, model downloads, compilation, and installation to
  be silent for many minutes or hours;
- use low-cost, read-only progress checks when useful, such as command or
  container liveness, output/cache growth, partial-file sizes and mtimes, or
  the appearance of expected artifacts;
- capture the final exit status and summarize only the relevant stdout/stderr;
  and
- return to the primary agent only on a terminal condition: success, clear
  failure, or required primary/user intervention.

An intermediate phase transition, a fixed amount of elapsed time, or a period
with no new output is not a terminal condition. The monitoring agent must not
send a final response while the monitored command is still running merely
because its own preferred monitoring interval elapsed. It should not impose a
short self-selected deadline on work expected to take a long time.

The monitoring agent must NOT make architecture, debugging, implementation,
or recovery decisions. If the operation clearly fails, it reports the evidence
and returns control; the primary agent decides what to do next. It should stay
quiet during normal progress unless the primary agent explicitly requests an
intermediate report.

After delegation, the primary agent must wait with
`wait_agent(timeout_ms=3600000)`, which waits for up to one hour and returns
earlier if the monitoring agent finishes or needs attention. The primary agent
must not poll with shorter waits, call status/list tools to check on the
monitor, or send messages merely to ask for progress. If the one-hour wait
expires and the monitor is still working, start another one-hour wait without
pinging it. This preserves expensive primary-agent tokens for decisions rather
than duplicating passive monitoring.

When the monitor reports a terminal result, the primary agent may perform one
bounded final verification required by the applicable runbook or skill. Do not
repeat the monitor's ongoing progress checks.
