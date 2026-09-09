#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_DIR / "run-recipe.py"
SPEC = importlib.util.spec_from_file_location("recipe_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RecipeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.builds = self.root / "builds"
        self.builds.mkdir()
        self.sources = self.root / ".build-sources"
        self.script_dir = self.root
        self.patchers = [
            mock.patch.object(RUNNER, "BUILDS_DIR", self.builds),
            mock.patch.object(RUNNER, "BUILD_SOURCES_DIR", self.sources),
            mock.patch.object(RUNNER, "SCRIPT_DIR", self.script_dir),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def write_yaml(self, path: Path, value: dict):
        path.write_text(yaml.safe_dump(value, sort_keys=False))

    def recipe(self, **extra):
        value = {
            "recipe_version": "1",
            "name": "Test recipe",
            "command": "server --port {port}",
        }
        value.update(extra)
        path = self.root / "recipe.yaml"
        self.write_yaml(path, value)
        return path

    def test_legacy_container_recipe_is_unchanged(self):
        loaded = RUNNER.load_recipe(
            self.recipe(container="existing:tag", build_args=["--no-cache"])
        )

        self.assertEqual(loaded["container"], "existing:tag")
        self.assertEqual(loaded["build_args"], ["--no-cache"])
        self.assertNotIn("_build_definition", loaded)

    def test_named_build_resolves_the_owned_image(self):
        self.write_yaml(
            self.builds / "external.yaml",
            {
                "build_version": "1",
                "image": "external:local",
                "method": "git",
                "repository": "https://example.invalid/source.git",
                "ref": "v1.2.3",
                "command": ["bash", "build.sh"],
                "env": {"IMAGE": "{image}"},
            },
        )

        loaded = RUNNER.load_recipe(self.recipe(build="external"))

        self.assertEqual(loaded["container"], "external:local")
        self.assertEqual(loaded["_build_definition"]["_id"], "external")

    def test_recipe_cannot_override_a_build_image(self):
        self.write_yaml(
            self.builds / "shared.yaml",
            {
                "build_version": "1",
                "image": "shared:local",
                "method": "pull",
                "source": "registry.invalid/shared:v1",
            },
        )

        with self.assertRaises(SystemExit):
            RUNNER.load_recipe(
                self.recipe(build="shared", container="misspelled:local")
            )

    def test_duplicate_output_images_are_rejected(self):
        for build_id in ("first", "second"):
            self.write_yaml(
                self.builds / f"{build_id}.yaml",
                {
                    "build_version": "1",
                    "image": "collision:local",
                    "method": "pull",
                    "source": f"registry.invalid/{build_id}:v1",
                },
            )

        with self.assertRaises(SystemExit):
            RUNNER.load_build_definition("first")

    def test_dockerfile_build_uses_declared_context_and_arguments(self):
        context = self.root / "builds" / "local"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM scratch\n")
        definition = {
            "_id": "local",
            "image": "local:test",
            "method": "dockerfile",
            "context": "builds/local",
            "dockerfile": "Dockerfile",
            "args": {"FEATURE": "enabled"},
        }

        with (
            mock.patch.object(RUNNER, "_run_command", return_value=True) as run,
            mock.patch.object(RUNNER, "check_image_exists", return_value=True),
        ):
            result = RUNNER.build_image(
                "local:test", build_definition=definition
            )

        self.assertTrue(result)
        run.assert_called_once_with(
            [
                "docker",
                "build",
                "-t",
                "local:test",
                "-f",
                str(context / "Dockerfile"),
                "--build-arg",
                "FEATURE=enabled",
                str(context),
            ]
        )


if __name__ == "__main__":
    unittest.main()
