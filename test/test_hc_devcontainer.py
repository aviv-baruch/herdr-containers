import importlib.machinery
import importlib.util
import copy
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

sys.dont_write_bytecode = True  # loading bin/hc-devcontainer would leave bin/__pycache__
path = Path(__file__).resolve().parents[1] / "bin" / "hc-devcontainer"
loader = importlib.machinery.SourceFileLoader("hc_devcontainer", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


def done(stdout="", code=0, stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class MetadataUserTests(unittest.TestCase):
    def test_last_remote_user_wins(self):
        raw = '[{"containerUser":"root","remoteUser":"first"},{"remoteUser":"vscode"}]'
        self.assertEqual(helper.metadata_user(raw), "vscode")

    def test_remote_user_beats_later_container_user(self):
        raw = '[{"remoteUser":"vscode"},{"containerUser":"root"}]'
        self.assertEqual(helper.metadata_user(raw), "vscode")

    def test_last_container_user_fallback(self):
        raw = '[{"containerUser":"root"},{"containerUser":"node"}]'
        self.assertEqual(helper.metadata_user(raw), "node")

    def test_missing_or_bad_metadata(self):
        self.assertIsNone(helper.metadata_user(None))
        self.assertIsNone(helper.metadata_user("not json"))

    def test_feature_id(self):
        for good in ("ghcr.io/herdrdev/features/herdr-core:1", "./herdr-core", "ghcr.io/o/r/herdr-core@sha256:ab"):
            self.assertTrue(helper.metadata_has_feature(json.dumps([{"id": good}])), good)
        for bad in ("other", "ghcr.io/o/not-herdr-core:1", "./herdr-core-extras", "herdr-core/x"):
            self.assertFalse(helper.metadata_has_feature(json.dumps([{"id": bad}])), bad)
        self.assertFalse(helper.metadata_has_feature("bad json"))


class NameTests(unittest.TestCase):
    def test_name_rules(self):
        self.assertEqual(helper.machine_name("/work/My Project!"), "my-project-")
        self.assertEqual(helper.machine_name("/work/.Hidden"), "hidden")
        self.assertEqual(helper.machine_name("/work/t7"), "container")
        self.assertEqual(helper.machine_name("/work/" + "x" * 40), "x" * 32)
        self.assertEqual(helper.machine_name("/work/---"), "container")


class ArgvTests(unittest.TestCase):
    def test_user_and_line_are_separate_args(self):
        line = "printf 'hi'; echo '$HOME'"
        self.assertEqual(helper.exec_argv("docker", "abc", "vscode", line),
                         ["docker", "exec", "-i", "-u", "vscode", "abc", "sh", "-c", line])

    def test_no_user(self):
        self.assertEqual(helper.exec_argv("podman", "abc", None, "true"),
                         ["podman", "exec", "-i", "abc", "sh", "-c", "true"])


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.dev = {
            "Id": "dev-id", "Name": "/dev-work", "Created": "2026-10-30",
            "State": {"Status": "running", "Running": True},
            "Config": {"Labels": {"devcontainer.local_folder": "/home/me/My Project",
                                  "devcontainer.metadata": '[{"remoteUser":"vscode"}]'},
                       "WorkingDir": "/fallback", "Image": "dev:latest"},
            "Mounts": [{"Source": "/home/me/My Project", "Destination": "/workspaces/project"}],
        }
        self.plain = {
            "Id": "plain-id", "Name": "/My Project", "Created": "2026-10-29",
            "State": {"Status": "running", "Running": True},
            "Config": {"Labels": {}, "WorkingDir": "/app", "User": "builder", "Image": "app:latest"},
            "Mounts": [],
        }

    def test_all_rows_workspace_via_user_and_unique_names(self):
        dev, plain = helper.rows([self.plain, self.dev])
        self.assertEqual(dev["kind"], "devcontainer")
        self.assertEqual(dev["state"], "running")
        self.assertFalse(dev["has_feature"])
        self.assertEqual(dev["workspace"], "/workspaces/project")
        self.assertEqual(dev["user"], "vscode")
        self.assertEqual(dev["via"], "hc-devcontainer '/home/me/My Project'")
        self.assertEqual(dev["suggested_name"], "my-project")
        self.assertEqual(plain["kind"], "container")
        self.assertEqual(plain["state"], "running")
        self.assertFalse(plain["has_feature"])
        self.assertEqual(plain["workspace"], "/app")
        self.assertEqual(plain["user"], "builder")
        self.assertEqual(plain["via"], "hc-devcontainer --container 'My Project'")
        self.assertEqual(plain["suggested_name"], "my-project-2")

    def test_workspace_fallback(self):
        self.dev["Mounts"] = []
        self.assertEqual(helper.rows([self.dev])[0]["workspace"], "/fallback")

    def test_subfolder_uses_closest_bind_mount(self):
        dev = copy.deepcopy(self.dev)
        dev["Config"]["Labels"]["devcontainer.local_folder"] = "/home/me/project/test"
        dev["Mounts"] = [
            {"Type": "bind", "Source": "/home/me", "Destination": "/workspaces/home"},
            {"Type": "bind", "Source": "/home/me/project", "Destination": "/workspaces/project"},
            {"Type": "bind", "Source": "/home/me/project/tes", "Destination": "/wrong"},
            {"Type": "volume", "Source": "/home/me/project/test", "Destination": "/volume"},
        ]
        self.assertEqual(helper.rows([dev])[0]["workspace"], "/workspaces/project/test")

    def test_symlinked_label_still_finds_its_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            (real / "sub").mkdir(parents=True)
            (Path(temp) / "link").symlink_to(real)
            mounts = [{"Type": "bind", "Source": str(real), "Destination": "/workspaces/real"}]
            self.assertEqual(helper.workspace_for(f"{temp}/link/sub", mounts, "/"), "/workspaces/real/sub")

    def test_working_dir_root_means_no_folder(self):
        dev = copy.deepcopy(self.dev)
        dev["Mounts"] = []
        dev["Config"]["WorkingDir"] = "/"
        self.assertEqual(helper.rows([dev])[0]["workspace"], "")

    def test_metadata_only_is_a_plain_container_with_the_metadata_user(self):
        # A plain `docker run` of an image the devcontainers tooling built still carries its metadata.
        dev = copy.deepcopy(self.dev)
        dev["Config"]["Labels"] = {"devcontainer.metadata": '[{"remoteUser":"vscode"},{"id":"./herdr-core"}]'}
        dev["Config"]["User"] = "root"
        dev["Config"]["WorkingDir"] = "/"
        dev["Mounts"] = []
        row = helper.rows([dev])[0]
        self.assertEqual((row["kind"], row["folder"], row["workspace"], row["has_feature"]), ("container", "", "", False))
        self.assertEqual(row["user"], "vscode")
        self.assertEqual(row["via"], "hc-devcontainer --container 'dev-work'")
        with patch.object(helper, "inspect", return_value=[dev]) as inspect, \
                patch.object(helper.os, "execvp") as execvp:
            self.assertEqual(helper.main(["--container", "dev-work", "true"]), 0)
        inspect.assert_called_once_with("docker", ["dev-work"])
        execvp.assert_called_once_with("docker", ["docker", "exec", "-i", "-u", "vscode",
                                                   "dev-work", "sh", "-c", "true"])

    def test_folderless_vsc_image_with_metadata_is_devcontainer(self):
        for image in ("vsc-acme-abc", "vsc-acme-abc-uid", "vsc-acme-abc-features"):
            with self.subTest(image=image):
                dev = copy.deepcopy(self.dev)
                dev["Name"] = "/frosty_rubin"
                dev["Config"]["Image"] = image
                dev["Config"]["Labels"] = {"devcontainer.metadata": '[{"remoteUser":"vscode"}]'}
                dev["Mounts"] = []
                row = helper.rows([dev])[0]
                self.assertEqual((row["kind"], row["folder"], row["user"]), ("devcontainer", "", "vscode"))
                self.assertEqual(row["via"], "hc-devcontainer --container 'frosty_rubin'")
        with patch.object(helper, "inspect", return_value=[dev]), patch.object(helper.os, "execvp") as execvp:
            helper.main(["--container", "frosty_rubin", "true"])
        self.assertEqual(execvp.call_args.args[1][4:7], ["vscode", "frosty_rubin", "sh"])

    def test_metadata_on_non_vsc_image_stays_plain(self):
        dev = copy.deepcopy(self.dev)
        dev["Config"]["Image"] = "mcr.microsoft.com/devcontainers/base"
        dev["Config"]["Labels"] = {"devcontainer.metadata": '[{"remoteUser":"vscode"}]'}
        self.assertEqual(helper.rows([dev])[0]["kind"], "container")

    def test_legacy_folder_label_connects_by_folder(self):
        dev = copy.deepcopy(self.dev)
        dev["Config"]["Labels"] = {"vsch.local.folder": "/home/me/My Project"}
        dev["Config"]["User"] = "builder"
        row = helper.rows([dev])[0]
        self.assertEqual(row["kind"], "devcontainer")
        self.assertEqual(row["folder"], "/home/me/My Project")
        self.assertEqual(row["user"], "builder")
        self.assertEqual(row["via"], "hc-devcontainer '/home/me/My Project'")
        with patch.object(helper, "inspect", return_value=[dev]), \
                patch.object(helper, "folder_ids", return_value=["dev-id"]) as folder_ids, \
                patch.object(helper.os, "execvp") as execvp:
            self.assertIsNone(helper.main(["/home/me/My Project", "true"]))
        folder_ids.assert_called_once_with("docker")
        execvp.assert_called_once_with("docker", ["docker", "exec", "-i", "-u", "builder",
                                                   "dev-id", "sh", "-c", "true"])

    def test_ls_via_round_trips_through_sh_for_odd_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "it's real"
            real.mkdir()
            (Path(temp) / "link").symlink_to(real)
            for label in (f"{temp}/link", f"{real}/", f"{real}/../{real.name}"):
                dev = copy.deepcopy(self.dev)
                dev["Config"]["Labels"]["devcontainer.local_folder"] = label
                with patch.object(helper, "inspect", return_value=[dev]), \
                        patch.object(helper, "ps", return_value=["dev-id"]):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        helper.main(["ls", "--all", "--json"])
                    via = json.loads(output.getvalue())[0]["via"]
                    # hc runs: sh -c '<via> "$1"' sh <line>
                    argv = shlex.split(via)[1:] + ["true"]
                    with patch.object(helper.os, "execvp") as execvp:
                        helper.main(argv)
                execvp.assert_called_once_with("docker", ["docker", "exec", "-i", "-u", "vscode",
                                                           "dev-id", "sh", "-c", "true"])

    def test_newest_of_several_matches_wins_and_warns(self):
        old = copy.deepcopy(self.dev)
        old["Id"], old["Created"] = "old-id", "2026-10-01T00:00:00Z"
        new = copy.deepcopy(self.dev)
        new["Id"], new["Created"] = "new-id", "2026-10-30T00:00:00Z"
        with patch.object(helper, "inspect", return_value=[old, new]), \
                patch.object(helper, "folder_ids", return_value=[]), \
                patch.object(helper.os, "execvp") as execvp:
            errors = io.StringIO()
            with redirect_stderr(errors):
                helper.main(["/home/me/My Project", "true"])
        self.assertEqual(execvp.call_args.args[1][-4], "new-id")
        self.assertIn("2 containers match; using newest new-id", errors.getvalue())

    def test_shell_quote_handles_apostrophe(self):
        self.assertEqual(helper.shell_quote("a'b"), "'a'\\''b'")

    def test_ls_all_json(self):
        with patch.object(helper, "inspect", return_value=[self.plain, self.dev]), \
                patch.object(helper, "ps", return_value=[]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(helper.main(["ls", "--all", "--json"]), 0)
            listed = json.loads(output.getvalue())
        self.assertEqual([r["id"] for r in listed], ["dev-id", "plain-id"])
        self.assertNotIn("created", listed[0])

    def test_ls_all_finds_stopped_folderless_vsc_but_not_stopped_plain_metadata(self):
        vsc = copy.deepcopy(self.dev)
        vsc["Config"]["Image"] = "vsc-acme-abc-features"
        vsc["Config"]["Labels"] = {"devcontainer.metadata": '[{"remoteUser":"vscode"}]'}
        vsc["State"] = {"Status": "exited", "Running": False}
        plain = copy.deepcopy(vsc)
        plain["Id"] = "plain-metadata"
        plain["Config"]["Image"] = "mcr.microsoft.com/devcontainers/base"
        with patch.object(helper, "inspect", return_value=[plain, vsc]), \
                patch.object(helper, "ps", return_value=[]):
            output = io.StringIO()
            with redirect_stdout(output):
                helper.main(["ls", "--all", "--json"])
        self.assertEqual([r["id"] for r in json.loads(output.getvalue())], ["dev-id"])

    def test_ls_without_all_lists_running_dev_containers_only(self):
        paused = copy.deepcopy(self.dev)
        paused["Id"], paused["State"] = "paused-id", {"Status": "paused", "Running": True}
        with patch.object(helper, "inspect", return_value=[self.plain, self.dev, paused]), \
                patch.object(helper, "ps", return_value=[]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(helper.main(["ls", "--json"]), 0)
        self.assertEqual([r["id"] for r in json.loads(output.getvalue())], ["dev-id"])

    def test_rows_keep_stopped_and_paused_state(self):
        dev = copy.deepcopy(self.dev)
        plain = copy.deepcopy(self.plain)
        dev["State"] = {"Status": "exited", "Running": False}
        plain["State"] = {"Status": "paused", "Running": True}
        dev["Config"]["Labels"]["devcontainer.metadata"] = '[{"id":"./herdr-core"}]'
        self.assertEqual([(r["id"], r["state"], r["has_feature"]) for r in helper.rows([dev, plain])],
                         [("dev-id", "exited", True), ("plain-id", "paused", False)])

    def test_docker_queries(self):
        def calls(argv, outputs):
            with patch.object(helper.subprocess, "run", side_effect=[done(out) for out in outputs]) as run, \
                    patch.object(helper.os, "execvp"), redirect_stdout(io.StringIO()):
                try:
                    helper.main(argv)
                except RuntimeError:
                    pass
            return [call.args[0] for call in run.call_args_list]

        self.assertEqual(calls(["ls", "--all", "--json"], ["plain-id\n", "dev-id\n", "dev-id\n", "dev-id\n", "[]"]), [
            ["docker", "ps", "-q"],
            ["docker", "ps", "-q", "-a", "--filter", "label=devcontainer.local_folder"],
            ["docker", "ps", "-q", "-a", "--filter", "label=vsch.local.folder"],
            ["docker", "ps", "-q", "-a", "--filter", "label=devcontainer.metadata"],
            ["docker", "inspect", "--type", "container", "plain-id", "dev-id"],
        ])
        # A dial looks only at the containers it could mean.
        self.assertEqual(calls(["--container", "web", "true"], ["[]"]), [
            ["docker", "inspect", "--type", "container", "web"],
        ])
        self.assertEqual(calls(["/w", "true"], ["a\n", "b\n", "[]"]), [
            ["docker", "ps", "-q", "--filter", "label=devcontainer.local_folder"],
            ["docker", "ps", "-q", "--filter", "label=vsch.local.folder"],
            ["docker", "inspect", "--type", "container", "a", "b"],
        ])

    def test_docker_down_or_missing_is_an_error(self):
        with patch.object(helper.subprocess, "run", return_value=done(code=1, stderr="Cannot connect to the Docker daemon")):
            with self.assertRaisesRegex(RuntimeError, "Cannot connect"):
                helper.main(["/w", "true"])
        with patch.object(helper.subprocess, "run", side_effect=FileNotFoundError("no docker")):
            with self.assertRaisesRegex(RuntimeError, "cannot run docker"):
                helper.main(["ls", "--json"])

    def test_container_connect_argv(self):
        line = "printf 'hello'; echo $HOME"
        with patch.object(helper, "inspect", return_value=[self.plain]), \
                patch.object(helper.os, "execvp") as execvp:
            self.assertEqual(helper.main(["--container", "My Project", line]), 0)
        execvp.assert_called_once_with("docker", ["docker", "exec", "-i", "-u", "builder",
                                                   "My Project", "sh", "-c", line])

    def test_container_must_be_running(self):
        stopped = {**self.plain, "State": {"Status": "exited", "Running": False}}
        with patch.object(helper, "inspect", return_value=[stopped]):
            with self.assertRaisesRegex(RuntimeError, "not running"):
                helper.main(["--container", "My Project", "true"])

    def test_start_argv_and_failure(self):
        with patch.object(helper.subprocess, "run", return_value=done()) as run:
            self.assertEqual(helper.main(["start", "dev-id"]), 0)
        run.assert_called_once_with(["docker", "start", "dev-id"], capture_output=True, text=True, check=False)
        with patch.object(helper.subprocess, "run", return_value=done(code=1, stderr="no such container")):
            with self.assertRaisesRegex(RuntimeError, "no such container"):
                helper.main(["start", "missing"])

    def test_exists_plain_codes(self):
        with patch.object(helper.subprocess, "run", return_value=done("[{}]")) as run:
            self.assertEqual(helper.main(["exists", "hc-devcontainer --container 'My Project'"]), 0)
        run.assert_called_once_with(["docker", "inspect", "--type", "container", "My Project"],
                                    capture_output=True, text=True, check=False)
        for wording in ("Error: No such object: gone", "Error: No such container: gone",
                        "Error: no such container gone"):  # docker, older docker, podman
            with patch.object(helper.subprocess, "run", return_value=done(code=1, stderr=wording)):
                self.assertEqual(helper.main(["exists", "hc-devcontainer --container gone"]), 1)
        with patch.object(helper.subprocess, "run", return_value=done(code=1, stderr="Cannot connect to the Docker daemon")), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(helper.main(["exists", "hc-devcontainer --container x"]), 2)
        self.assertIn("Cannot connect", errors.getvalue())

    def test_exists_folder_any_state_and_form(self):
        stopped = copy.deepcopy(self.dev)
        stopped["State"] = {"Status": "exited", "Running": False}
        with patch.object(helper, "inspect", return_value=[stopped]), \
                patch.object(helper, "folder_ids", return_value=["dev-id"]) as folder_ids:
            self.assertEqual(helper.main(["exists", "hc-devcontainer '/home/me/My Project'"]), 0)
            self.assertEqual(helper.main(["exists", "hc-devcontainer /home/me/My\\ Project/"]), 0)
            self.assertEqual(helper.main(["exists", "hc-devcontainer /home/me/other"]), 1)
        folder_ids.assert_called_with("docker", "-a")

    def test_exists_rejects_other_commands(self):
        for via in ("ssh box", "hc-devcontainer", "hc-devcontainer a b", "hc-devcontainer 'open"):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(helper.main(["exists", via]), 2, via)


if __name__ == "__main__":
    unittest.main()
