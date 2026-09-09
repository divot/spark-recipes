# Image Builds

Build definitions give recipes a reusable way to prepare a named Docker image.
The build ID is the YAML filename without its extension. For example, a recipe
selects `builds/example.yaml` with:

```yaml
build: example
```

The recipe must omit `container` and `build_args`; the build definition owns
the image name. Image names must be unique across all build definitions, so two
different build IDs cannot silently build the same tag.

All definitions use schema version 1 and one of four methods.

## Repository Dockerfile

Both paths are repository-relative. `dockerfile` is relative to `context`.

```yaml
build_version: "1"
image: example:local
method: dockerfile
context: builds/example
dockerfile: Dockerfile
args:
  OPTIONAL_BUILD_ARG: value
```

This runs `docker build` directly and is suitable for Dockerfiles maintained in
this repository.

## External Git Repository

```yaml
build_version: "1"
image: example:local
method: git
repository: https://github.com/example/project.git
ref: 0123456789abcdef0123456789abcdef01234567
command: [bash, build.sh]
env:
  IMAGE: "{image}"
```

Sources are checked out at a detached ref under the gitignored
`.build-sources/<build-id>/` directory. Existing checkouts must have the
declared origin and no local changes. The command is executed directly, without
a shell wrapper; its repository is trusted build code. `{image}` in an
environment value expands to the definition's image name.

## Existing Registry Image

```yaml
build_version: "1"
image: example:local
method: pull
source: registry.example.com/example:version
```

The source image is pulled and tagged as `image` when the names differ.

## Existing vLLM Builder

```yaml
build_version: "1"
image: vllm-node-custom
method: vllm
args: [--no-cache]
```

This delegates to `build-and-copy.sh`, preserving the existing project-specific
vLLM build path. Recipes that declare `container` and optional `build_args`
continue to use that legacy path without a build definition.

`run-recipe.py <recipe> --setup` prepares a missing image. Add `--force-build`
to rebuild it even when its tag already exists. A dry run with those flags
prints the selected build and command without cloning or building.
