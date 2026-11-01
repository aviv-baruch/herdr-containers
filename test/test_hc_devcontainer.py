import importlib.machinery
import importlib.util
import copy
import io
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import unittest.mock
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

sys.dont_write_bytecode = True  # loading the helper would leave __pycache__
path = Path(__file__).resolve().parents[1] / "requirements" / "hc-devcontainer"
loader = importlib.machinery.SourceFileLoader("hc_devcontainer", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


def setUpModule():
    # Connecting starts a real background `serve`; tests that want it patch it again.
    patcher = patch.object(helper, "serve_in_background")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


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

    def test_remote_env_merges_in_order_and_null_removes(self):
        dev = {
            "Config": {"Env": ["PATH=/usr/bin:/bin", "BASE=container"], "Labels": {
                "devcontainer.local_folder": "/work",
                "devcontainer.metadata": json.dumps([
                    {"remoteEnv": {"KEEP": "first", "DROP": "old", "SHELL": "/bin/fish"}},
                    {"remoteEnv": {"KEEP": "last", "DROP": None, "FROM_BASE": "${containerEnv:BASE}"}},
                ]),
            }}, "Id": "dev", "Name": "/dev",
        }
        self.assertEqual(helper.rows([dev])[0]["remote_env"],
                         {"KEEP": "last", "SHELL": "/bin/fish", "FROM_BASE": "container"})

    def test_remote_env_substitutions_and_defaults(self):
        dev = {
            "Config": {"Env": ["PATH=/usr/bin:/bin", "EMPTY="], "Labels": {
                "devcontainer.local_folder": "/work",
                "devcontainer.metadata": json.dumps([{"remoteEnv": {
                    "PATH": "${containerEnv:PATH}:/x",
                    "CONTAINER_DEFAULT": "${containerEnv:MISSING:/default}",
                    "CONTAINER_EMPTY": "${containerEnv:EMPTY:fallback}",
                    "HOST": "${localEnv:HOST_VALUE}",
                    "HOST_DEFAULT": "${localEnv:MISSING:/host-default}",
                    "MISSING": "${containerEnv:UNKNOWN}/${localEnv:MISSING}",
                    "OTHER": "${workspaceFolder}/${env:KEEP}",
                }}]),
            }}, "Id": "dev", "Name": "/dev",
        }
        with patch.dict(os.environ, {"HOST_VALUE": "from-host"}, clear=True):
            remote = helper.rows([dev])[0]["remote_env"]
        self.assertEqual(remote, {
            "PATH": "/usr/bin:/bin:/x", "CONTAINER_DEFAULT": "/default",
            "CONTAINER_EMPTY": "", "HOST": "from-host", "HOST_DEFAULT": "/host-default",
            "MISSING": "/", "OTHER": "${workspaceFolder}/${env:KEEP}",
        })


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
        argv = helper.exec_argv("docker", "abc", "vscode", line)
        self.assertEqual(argv[:8], ["docker", "exec", "-i", "-u", "vscode", "abc", "sh", "-c"])
        self.assertEqual(argv[-2:], ["sh", line])
        self.assertIn('"$1"', argv[-3])
        self.assertNotIn(line, argv[-3])

    def test_no_user(self):
        argv = helper.exec_argv("podman", "abc", None, "true")
        self.assertEqual(argv[:6], ["podman", "exec", "-i", "abc", "sh", "-c"])
        self.assertEqual(argv[-2:], ["sh", "true"])

    def test_remote_env_argv_has_names_only(self):
        argv = helper.exec_argv("docker", "abc", None, "true",
                                {"TOKEN": "secret-value", "9BAD": "invalid", "BAD-NAME": "invalid",
                                 None: "invalid"})
        self.assertEqual(argv[:6], ["docker", "exec", "-i", "-e", "TOKEN", "abc"])
        self.assertNotIn("secret-value", argv)
        self.assertNotIn("9BAD", argv)
        self.assertNotIn("BAD-NAME", argv)

    def test_wrapper_runs_quoted_line_and_sets_shell_only_when_missing(self):
        line = (
            "printf '<%s>\\n' 'a \"quoted\" value'; "
            "env | awk -F= '$1 == \"SHELL\" { print; found=1; exit } "
            "END { if (!found) print \"SHELL=unset\" }'"
        )
        argv = helper.exec_argv("docker", "abc", None, line)
        with tempfile.TemporaryDirectory() as temp:
            fake_id = Path(temp) / "id"
            fake_id.write_text("#!/bin/sh\nprintf '%s\\n' testuser\n")
            fake_id.chmod(0o755)
            fake_getent = Path(temp) / "getent"
            fake_getent.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = passwd ] && [ \"$2\" = testuser ] && [ \"$PASSWD_FOUND\" = 1 ] || exit 1\n"
                "printf '%s\\n' 'other:x:1:1::/home/other:/wrong' "
                "'testuser:x:1000:1000::/home/testuser:/bin/zsh'\n"
            )
            fake_getent.chmod(0o755)
            base_env = {**os.environ, "PATH": f"{temp}:{os.environ['PATH']}", "PASSWD_FOUND": "1"}
            base_env.pop("SHELL", None)
            for shell, found, expected in (
                (None, "1", '<a "quoted" value>\nSHELL=/bin/zsh\n'),
                ("/existing/shell", "1", '<a "quoted" value>\nSHELL=/existing/shell\n'),
                ("", "1", '<a "quoted" value>\nSHELL=/bin/zsh\n'),
                (None, "0", '<a "quoted" value>\nSHELL=unset\n'),
            ):
                with self.subTest(shell=shell, found=found):
                    env = {**base_env, "PASSWD_FOUND": found}
                    if shell is not None:
                        env["SHELL"] = shell
                    script = ("unset SHELL; " if shell is None else "") + argv[-3]
                    result = subprocess.run(["/bin/sh", "-c", script, *argv[-2:]], env=env,
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)

    def test_wrapper_uses_passwd_without_getent(self):
        user, *_, shell = next(line.split(":") for line in Path("/etc/passwd").read_text().splitlines()
                               if line.count(":") == 6 and line.split(":")[6])
        argv = helper.exec_argv("docker", "abc", None, 'printf "%s\\n" "$SHELL"')
        with tempfile.TemporaryDirectory() as temp:
            fake_id = Path(temp) / "id"
            fake_id.write_text(f"#!/bin/sh\nprintf '%s\\n' '{user}'\n")
            fake_id.chmod(0o755)
            (Path(temp) / "awk").symlink_to(shutil.which("awk"))
            (Path(temp) / "sh").symlink_to("/bin/sh")
            result = subprocess.run(["/bin/sh", "-c", "unset SHELL; " + argv[-3], *argv[-2:]],
                                    env={"PATH": temp}, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, shell + "\n")

    def test_workspace_is_its_own_arg_and_only_when_known(self):
        line = "pwd"
        self.assertEqual(helper.exec_argv("docker", "abc", None, line, workspace="/w s")[-3:], ["sh", line, "/w s"])
        self.assertEqual(helper.exec_argv("docker", "abc", None, line, workspace="")[-2:], ["sh", line])
        self.assertEqual(helper.exec_argv("docker", "abc", None, line, workspace=None)[-2:], ["sh", line])

    def test_wrapper_starts_in_the_workspace_and_survives_a_missing_one(self):
        with tempfile.TemporaryDirectory() as temp:
            odd = Path(temp) / "it's a \"dir\" $HOME"
            odd.mkdir()
            elsewhere = Path(temp) / "elsewhere"
            elsewhere.mkdir()
            env = {**os.environ, "SHELL": "/bin/sh"}
            for workspace, expected in ((str(odd), str(odd)), (f"{temp}/missing", str(elsewhere)),
                                        ("", str(elsewhere))):
                with self.subTest(workspace=workspace):
                    argv = helper.exec_argv("docker", "abc", None, "pwd -P", workspace=workspace)
                    script = argv[argv.index("-c") + 1]
                    result = subprocess.run(["/bin/sh", "-c", script, *argv[argv.index("-c") + 2:]],
                                            cwd=elsewhere, env=env, capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, os.path.realpath(expected) + "\n")
                    self.assertEqual(result.stderr, "")


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

    def test_repo_mounted_at_a_workspaces_folder(self):
        mounts = [
            {"Type": "volume", "Source": "/var/lib/docker/volumes/uv/_data", "Destination": "/home/vscode/.cache/uv"},
            {"Type": "volume", "Source": "/var/lib/docker/volumes/venv/_data", "Destination": "/workspace/.venv"},
            {"Type": "volume", "Source": "/var/lib/docker/volumes/repo/_data", "Destination": "/workspaces/service"},
        ]
        self.assertEqual(helper.workspace_for("", mounts, "/", True), "/workspaces/service")
        self.assertEqual(helper.workspace_for("/home/me/elsewhere", mounts, "", True), "/workspaces/service")
        self.assertEqual(helper.workspace_for("", mounts[:2], "/", True), "")
        self.assertEqual(helper.workspace_for("", [{"Type": "volume", "Destination": "/workspaces/a/b"}], "/", True), "")

    def test_image_working_dir_beats_a_guessed_workspaces_mount(self):
        mounts = [{"Type": "volume", "Source": "/var/lib/docker/volumes/c/_data", "Destination": "/workspaces/cache"}]
        self.assertEqual(helper.workspace_for("", mounts, "/app", True), "/app")
        self.assertEqual(helper.workspace_for("/elsewhere/cache", mounts, "/app", True), "/app")
        self.assertEqual(helper.workspace_for("", mounts, "/", True), "/workspaces/cache")

    def test_clone_in_volume_label_beats_working_dir(self):
        mounts = [{"Type": "volume", "Name": "vsc-remote-containers", "Destination": "/workspaces"}]
        self.assertEqual(helper.workspace_for("", mounts, "/app", True, "repo"), "/workspaces/repo")

    def test_plain_container_keeps_its_working_dir(self):
        mounts = [{"Type": "volume", "Source": "/var/lib/docker/volumes/c/_data", "Destination": "/workspaces/cache"}]
        self.assertEqual(helper.workspace_for("", mounts, "/app"), "/app")
        self.assertEqual(helper.workspace_for("", mounts, "/"), "")

    def test_matching_bind_mount_beats_a_workspaces_volume(self):
        mounts = [
            {"Type": "volume", "Source": "/var/lib/docker/volumes/repo/_data", "Destination": "/workspaces/other"},
            {"Type": "bind", "Source": "/home/me/project", "Destination": "/workspace"},
        ]
        self.assertEqual(helper.workspace_for("/home/me/project", mounts, "/", True), "/workspace")

    def test_several_workspaces_mounts_never_guess(self):
        mounts = [
            {"Type": "bind", "Source": "/srv/lib", "Destination": "/workspaces/shared-lib"},
            {"Type": "volume", "Source": "/var/lib/docker/volumes/x/_data", "Destination": "/workspaces/service"},
        ]
        self.assertEqual(helper.workspace_for("/elsewhere/service", mounts, "/", True), "/workspaces/service")
        self.assertEqual(helper.workspace_for("/elsewhere/service", mounts[::-1], "/", True), "/workspaces/service")
        self.assertEqual(helper.workspace_for("", mounts, "/app", True), "/app")
        self.assertEqual(helper.workspace_for("/elsewhere/other", mounts, "/", True), "")

    def test_clone_in_volume_container(self):
        # VS Code's Clone Repository in Container Volume (vscode-remote-release #1581, #2881).
        clone = {
            "Id": "clone-id", "Name": "/clone", "Created": "2026-10-31",
            "State": {"Status": "running", "Running": True},
            "Config": {"Labels": {"vsch.local.repository": "https://github.com/me/try-node.git",
                                  "vsch.local.repository.volume": "vsc-remote-containers",
                                  "vsch.local.repository.folder": "try-node",
                                  "devcontainer.metadata": '[{"remoteUser":"node"}]'},
                       "WorkingDir": "", "Image": "mcr.microsoft.com/devcontainers/javascript-node"},
            "Mounts": [{"Type": "volume", "Name": "vsc-remote-containers", "Destination": "/workspaces"}],
        }
        row = helper.rows([clone])[0]
        self.assertEqual((row["kind"], row["workspace"], row["user"]), ("devcontainer", "/workspaces/try-node", "node"))
        self.assertEqual(row["via"], "hc-devcontainer --container 'clone'")
        clone["Config"]["Labels"]["vsch.local.repository.folder"] = "../etc"
        self.assertEqual(helper.rows([clone])[0]["workspace"], "")

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
                patch.object(helper.os, "execvpe") as execvpe:
            self.assertEqual(helper.main(["--container", "dev-work", "true"]), 0)
        inspect.assert_called_once_with("docker", ["dev-work"])
        execvpe.assert_called_once_with("docker", helper.exec_argv("docker", "dev-work", "vscode", "true"),
                                        dict(os.environ))

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
        with patch.object(helper, "inspect", return_value=[dev]), patch.object(helper.os, "execvpe") as execvpe:
            helper.main(["--container", "frosty_rubin", "true"])
        self.assertEqual(execvpe.call_args.args[1][4:7], ["vscode", "frosty_rubin", "sh"])

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
                patch.object(helper.os, "execvpe") as execvpe:
            self.assertEqual(helper.main(["/home/me/My Project", "true"]), 0)
        folder_ids.assert_called_once_with("docker")
        execvpe.assert_called_once_with("docker", helper.exec_argv("docker", "dev-id", "builder", "true",
                                                                   workspace="/workspaces/project"),
                                        dict(os.environ))

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
                    with patch.object(helper.os, "execvpe") as execvpe:
                        helper.main(argv)
                # The bind doesn't match these labels; the image's WORKDIR beats a guessed mount.
                execvpe.assert_called_once_with("docker", helper.exec_argv("docker", "dev-id", "vscode", "true",
                                                                           workspace="/fallback"),
                                                dict(os.environ))

    def test_newest_of_several_matches_wins_and_warns(self):
        old = copy.deepcopy(self.dev)
        old["Id"], old["Created"] = "old-id", "2026-10-01T00:00:00Z"
        new = copy.deepcopy(self.dev)
        new["Id"], new["Created"] = "new-id", "2026-10-30T00:00:00Z"
        with patch.object(helper, "inspect", return_value=[old, new]), \
                patch.object(helper, "folder_ids", return_value=[]), \
                patch.object(helper.os, "execvpe") as execvpe:
            errors = io.StringIO()
            with redirect_stderr(errors):
                helper.main(["/home/me/My Project", "true"])
        self.assertEqual(execvpe.call_args.args[1][-7], "new-id")
        self.assertIn("2 containers match; using newest new-id", errors.getvalue())

    def test_shell_quote_handles_apostrophe(self):
        self.assertEqual(helper.shell_quote("a'b"), "'a'\\''b'")

    def test_ls_all_json(self):
        self.dev["Config"]["Labels"]["devcontainer.metadata"] = '[{"remoteEnv":{"TOKEN":"secret-value"}}]'
        with patch.object(helper, "inspect", return_value=[self.plain, self.dev]), \
                patch.object(helper, "ps", return_value=[]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(helper.main(["ls", "--all", "--json"]), 0)
            listed = json.loads(output.getvalue())
        self.assertEqual([r["id"] for r in listed], ["dev-id", "plain-id"])
        self.assertNotIn("created", listed[0])
        self.assertNotIn("remote_env", listed[0])
        self.assertNotIn("secret-value", output.getvalue())

    def test_remote_env_reaches_both_connect_paths_without_argv_values(self):
        dev = copy.deepcopy(self.dev)
        dev["Config"]["Env"] = ["PATH=/usr/bin:/bin"]
        dev["Config"]["Labels"]["devcontainer.metadata"] = json.dumps([{"remoteUser": "vscode", "remoteEnv": {
            "PATH": "${containerEnv:PATH}:/custom", "SHELL": "/bin/fish",
            "TOKEN": "secret-value", "BAD-NAME": "skip-me",
        }}])
        for command, container_id in ((["/home/me/My Project", "true"], "dev-id"),
                                      (["--container", "dev-work", "true"], "dev-work")):
            with self.subTest(command=command), \
                    patch.object(helper, "inspect", return_value=[dev]), \
                    patch.object(helper, "folder_ids", return_value=["dev-id"]), \
                    patch.object(helper.shutil, "which", return_value="/usr/bin/docker"), \
                    patch.object(helper.os, "execvpe") as execvpe:
                helper.main(command)
                executable, argv, env = execvpe.call_args.args
            self.assertEqual(executable, "/usr/bin/docker")
            self.assertEqual(argv[:11], ["docker", "exec", "-i", "-u", "vscode",
                                         "-e", "PATH", "-e", "SHELL", "-e", "TOKEN"])
            self.assertEqual(argv[11], container_id)
            self.assertNotIn("secret-value", argv)
            self.assertNotIn("skip-me", argv)
            self.assertEqual(env["PATH"], "/usr/bin:/bin:/custom")
            self.assertEqual(env["SHELL"], "/bin/fish")
            self.assertEqual(env["TOKEN"], "secret-value")
            self.assertNotIn("BAD-NAME", env)

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
                    patch.object(helper.os, "execvpe"), redirect_stdout(io.StringIO()):
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
                patch.object(helper.os, "execvpe") as execvpe:
            self.assertEqual(helper.main(["--container", "My Project", line]), 0)
        execvpe.assert_called_once_with("docker", helper.exec_argv("docker", "My Project", "builder", line,
                                                                   workspace="/app"),
                                        dict(os.environ))

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


class WindowsHostTests(unittest.TestCase):
    """Docker Desktop labels hold Windows paths; its mounts spell the drive in Linux forms."""

    def test_windows_folders_match_whatever_the_case_slashes_or_spelling(self):
        label = r"c:\Users\Me\My Proj"
        for same in ("C:/users/me/my proj/", r"C:\USERS\ME\MY PROJ", "/run/desktop/mnt/host/c/Users/Me/My Proj",
                     "/host_mnt/c/Users/Me/My Proj", "/mnt/c/Users/Me/My Proj"):
            self.assertTrue(helper.same_folder(label, same), same)
        for other in (r"c:\Users\Me\My Proj 2", r"d:\Users\Me\My Proj", "/Users/Me/My Proj"):
            self.assertFalse(helper.same_folder(label, other), other)

    def test_workspace_from_docker_desktop_mounts_keeps_the_case(self):
        label = r"c:\Users\Me\Proj\Sub Dir"
        for source in ("/run/desktop/mnt/host/c/Users/Me/Proj", "/host_mnt/c/users/me/proj",
                       "/mnt/c/Users/Me/Proj", r"C:\Users\Me\Proj"):
            mounts = [{"Type": "bind", "Source": source, "Destination": "/workspaces/Proj"}]
            self.assertEqual(helper.workspace_for(label, mounts, "/"), "/workspaces/Proj/Sub Dir", source)
        mounts = [{"Type": "bind", "Source": "/run/desktop/mnt/host/d/Users/Me/Proj", "Destination": "/wrong"}]
        self.assertEqual(helper.workspace_for(label, mounts, "/"), "")

    def test_names_and_guesses_use_the_windows_folder_name(self):
        self.assertEqual(helper.machine_name(r"c:\Users\me\My Proj" "\\"), "my-proj")
        mounts = [{"Type": "volume", "Destination": "/workspaces/a"}, {"Type": "volume", "Destination": "/workspaces/Proj"}]
        self.assertEqual(helper.workspace_for(r"c:\Users\me\Proj", mounts, "/", True), "/workspaces/Proj")

    def test_windows_via_finds_its_container(self):
        dev = {"Id": "dev-id", "Name": "/dev", "State": {"Status": "exited"},
               "Config": {"Labels": {"devcontainer.local_folder": r"c:\Users\Me\Proj"}}}
        with patch.object(helper, "inspect", return_value=[dev]), \
                patch.object(helper, "folder_ids", return_value=["dev-id"]):
            row = helper.rows([dev])[0]
            self.assertEqual(row["via"], r"hc-devcontainer 'c:\Users\Me\Proj'")
            self.assertEqual(helper.main(["exists", row["via"]]), 0)
            self.assertEqual(helper.main(["exists", "hc-devcontainer 'C:/users/me/proj'"]), 0)
            self.assertEqual(helper.main(["exists", r"hc-devcontainer 'c:\Users\Me\Other'"]), 1)

    def test_windows_runs_docker_exec_as_a_child_and_passes_its_code(self):
        row = {"user": "me", "workspace": "/w"}
        with patch.object(helper, "WINDOWS", True), patch.object(helper.os, "execvpe") as execvpe, \
                patch.object(helper, "Server") as server, patch.object(helper, "serve_in_background") as serve, \
                patch.object(helper.subprocess, "run", return_value=done(code=3)) as run:
            self.assertEqual(helper.exec_container("docker", row, "id", "true"), 3)
        execvpe.assert_not_called()
        # Windows keeps no orphan to watch the connection: it serves in-process and stops after.
        server.assert_called_once_with("docker", "id", "me")
        server.return_value.stop.assert_called_once_with()
        serve.assert_not_called()
        run.assert_called_once_with(helper.exec_argv("docker", "id", "me", "true", {}, "/w"),
                                    env=dict(os.environ), check=False)

    def test_windows_cmd_launcher_runs_the_helper_beside_it(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "requirements" / "windows" / "hc-devcontainer.cmd").read_text(encoding="utf-8")
        self.assertIn('"%~dp0hc-devcontainer.py" %*', launcher)
        self.assertIn("exit /b", launcher)
        self.assertNotIn("/dev/null", launcher)
        attributes = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn("*.cmd text eol=crlf", attributes)
        self.assertIn("requirements/windows/hc-devcontainer text eol=lf", attributes)

    def test_windows_sh_launcher_passes_args_untouched_to_the_helper_beside_it(self):
        # Core on Windows runs a via with Git for Windows' sh, which can't use the helper's python3 shebang.
        launcher = Path(__file__).resolve().parents[1] / "requirements" / "windows" / "hc-devcontainer"
        with tempfile.TemporaryDirectory() as temp:
            bin_dir, fake = Path(temp) / "bin", Path(temp) / "fake"
            bin_dir.mkdir()
            fake.mkdir()
            shutil.copy(launcher, bin_dir / "hc-devcontainer")
            (bin_dir / "hc-devcontainer.py").write_text(
                "import json, os, sys\n"
                "print(json.dumps([sys.argv[1:], os.environ.get('MSYS_NO_PATHCONV'), os.environ.get('MSYS2_ARG_CONV_EXCL')]))\n")
            (fake / "py").write_text(f'#!/bin/sh\n[ "$1" = -3 ] || exit 9\nshift\nexec {shlex.quote(sys.executable)} "$@"\n')
            (fake / "py").chmod(0o755)
            line = """sh -c 'p=$(command -v herdr-core) || p=$HOME/.local/bin/herdr-core; [ -x "$p" ] || exit 127'"""
            via = f"{bin_dir}/hc-devcontainer 'c:\\Users\\Me\\My Proj'"
            env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
            # Exactly how core runs a via: sh -c '<via> "$1"' sh <line>.
            result = subprocess.run(["sh", "-c", f'{via} "$1"', "sh", line], env=env,
                                    capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [["c:\\Users\\Me\\My Proj", line], "1", "*"])


def run_script(script, *args, env=None, **kwargs):
    return subprocess.run(["/bin/sh", "-c", script, "sh", *args], env=env, capture_output=True, text=True,
                          check=False, **kwargs)


def echo_server():
    """Uppercases what each client sends; closes a client on "bye"."""
    server = socket.create_server(("127.0.0.1", 0))

    def talk(client):
        with client:
            while (data := client.recv(4096)) and data != b"bye\n":
                client.sendall(data.upper())

    def loop():
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=talk, args=(client,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return server


def read_within(stream, seconds=5):
    ready, _, _ = select.select([stream], [], [], seconds)
    return os.read(stream.fileno(), 4096) if ready else b"(nothing)"


class BrowserTests(unittest.TestCase):
    def test_wrapper_sets_a_private_browser_helper_only_when_unset(self):
        argv = helper.exec_argv("docker", "abc", None, 'printf "%s\\n" "$BROWSER"')
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "PATH": os.environ["PATH"], "SHELL": "/bin/sh"}
            result = run_script(argv[-3], *argv[-1:], env=env)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            path = Path(home) / ".cache" / "herdr-containers" / "browser"
            self.assertEqual(result.stdout, f"{path}\n")
            self.assertEqual(path.read_text(), helper.BROWSER_HELPER)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["browser"])
            result = run_script(argv[-3], *argv[-1:], env={**env, "BROWSER": "mine"})
            self.assertEqual(result.stdout, "mine\n")
        with tempfile.TemporaryDirectory() as temp:
            unwritable = Path(temp) / "ro"
            unwritable.mkdir(mode=0o500)
            result = run_script(argv[-3], *argv[-1:], env={**env, "HOME": str(unwritable)})
            self.assertEqual(result.returncode, 0)
            if os.geteuid():  # root writes anyway
                self.assertEqual((result.stdout, result.stderr), ("\n", ""))

    def test_helper_queues_for_a_live_listener_and_prints_otherwise(self):
        with tempfile.TemporaryDirectory() as temp:
            browser = Path(temp) / "browser"
            browser.write_text(helper.BROWSER_HELPER)
            browser.chmod(0o755)
            result = subprocess.run([str(browser), "https://a.test/x"], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertEqual(result.stderr, "Open this link: https://a.test/x\n")
            (Path(temp) / "serve.lock").mkdir()
            (Path(temp) / "serve.lock" / "pid").write_text(f"{os.getpid()}\n")  # alive, not a listener
            result = subprocess.run([str(browser), "https://a.test/x"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            listener = subprocess.Popen(["/bin/sh", "-c", "# hc-devcontainer-serve\nsleep 30"])
            try:
                (Path(temp) / "serve.lock" / "pid").write_text(f"{listener.pid}\n")
                result = subprocess.run([str(browser), "https://a.test/1", "http://localhost:3000/"],
                                        capture_output=True, text=True)
            finally:
                listener.kill()
                listener.wait()
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual((Path(temp) / "open").read_text(), "https://a.test/1\nhttp://localhost:3000/\n")

    def test_listener_reports_opens_and_ports_and_ends_with_stdin(self):
        with tempfile.TemporaryDirectory() as home, echo_server() as server:
            port = server.getsockname()[1]
            folder = Path(home) / ".cache" / "herdr-containers"
            folder.mkdir(parents=True)
            (folder / "open").write_text("http://stale.test/\n")
            proc = subprocess.Popen(["/bin/sh", "-c", helper.LISTEN], env={**os.environ, "HOME": home},
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                first = proc.stdout.readline()
                self.assertTrue(first.startswith("ports "), first)
                self.assertEqual(helper.listening(first[6:]).get(port), "127.0.0.1", first)
                self.assertFalse((folder / "open").exists())  # stale requests are dropped
                (folder / "open").write_text("https://a.test/\n")
                self.assertEqual(proc.stdout.readline(), "open https://a.test/\n")
                second = run_script(helper.LISTEN, env={**os.environ, "HOME": home}, timeout=5)
                self.assertEqual(second.returncode, 3)
                (Path(home) / "file").write_text("")
                no_home = run_script(helper.LISTEN, env={**os.environ, "HOME": f"{home}/file"}, timeout=5)
                self.assertEqual(no_home.returncode, 4)  # serve stops instead of retrying
                proc.stdin.close()
                self.assertNotEqual(proc.wait(timeout=5), 0)
            finally:
                proc.kill()
                proc.communicate()
            # Its lock now names a dead process: the next listener takes over.
            proc = subprocess.Popen(["/bin/sh", "-c", helper.LISTEN], env={**os.environ, "HOME": home},
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertTrue(proc.stdout.readline().startswith("ports "))
                self.assertEqual((folder / "serve.lock" / "pid").read_text().strip(), str(proc.pid))
            finally:
                proc.communicate(timeout=5)

    def test_listening_keeps_loopback_and_wildcard(self):
        line = ("0100007F:0BB8 00000000:1F90 0A000002:0016 "
                "00000000000000000000000001000000:1435 00000000000000000000000000000000:0050 "
                "0000000000000000FFFF00000100007F:0051 0100007F:0000 junk 0100007F:zz")
        self.assertEqual(helper.listening(line), {3000: "127.0.0.1", 8080: "127.0.0.1", 5173: "::1",
                                                  80: "::1", 81: "127.0.0.1"})

    def test_relay_carries_bytes_with_each_container_tool(self):
        with tempfile.TemporaryDirectory() as temp, echo_server() as server:
            port = str(server.getsockname()[1])
            for tool in ("python3", "bash", "nc"):
                if not shutil.which(tool):
                    continue
                bin_dir = Path(temp) / tool
                bin_dir.mkdir()
                for name in (tool, "cat", "sh"):
                    (bin_dir / name).symlink_to(shutil.which(name))
                with self.subTest(tool=tool):
                    proc = subprocess.Popen(["/bin/sh", "-c", helper.RELAY, "sh", "127.0.0.1", port],
                                            env={"PATH": str(bin_dir)}, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE)
                    try:
                        proc.stdin.write(b"hello relay\n")
                        proc.stdin.flush()
                        self.assertEqual(read_within(proc.stdout), b"HELLO RELAY\n")
                        # The server hanging up ends the relay, so the host sees EOF.
                        proc.stdin.write(b"bye\n")
                        proc.stdin.flush()
                        if tool != "nc":
                            self.assertEqual(read_within(proc.stdout), b"")
                    finally:
                        proc.kill()
                        proc.communicate()
            empty = Path(temp) / "empty"
            empty.mkdir()
            self.assertEqual(run_script(helper.RELAY, "127.0.0.1", port, env={"PATH": str(empty)}).returncode, 127)

    def test_host_opener(self):
        with patch.dict(os.environ, {"HC_DEVCONTAINER_OPEN": "my-open --flag"}):
            self.assertEqual(helper.host_opener(), ["my-open", "--flag"])
        with patch.dict(os.environ, {"HC_DEVCONTAINER_OPEN": ""}), patch.object(helper.sys, "platform", "linux"):
            for release, found, expected in (
                ("6.6.87.2-microsoft-standard-WSL2", {"wslview": "/usr/bin/wslview"}, ["/usr/bin/wslview"]),
                ("6.6.87.2-microsoft-standard-WSL2", {"explorer.exe": "/mnt/c/WINDOWS/explorer.exe"},
                 ["/mnt/c/WINDOWS/explorer.exe"]),
                ("6.8.0-generic", {"wslview": "/usr/bin/wslview"}, ["xdg-open"]),
            ):
                with self.subTest(release=release, found=found), \
                        patch("builtins.open", unittest.mock.mock_open(read_data=release)), \
                        patch.object(helper.shutil, "which", side_effect=found.get):
                    self.assertEqual(helper.host_opener(), expected)
            with patch.object(helper.sys, "platform", "darwin"):
                self.assertEqual(helper.host_opener(), ["open"])
            with patch.object(helper, "WINDOWS", True):
                self.assertEqual(helper.host_opener(), ["explorer.exe"])

    def test_open_sends_web_links_to_the_host_and_maps_container_ports(self):
        server = helper.Server("docker", "abc", None)
        opened = []
        with patch.object(helper, "host_opener", return_value=["opener"]), \
                patch.object(helper.subprocess, "Popen", side_effect=lambda argv, **_: opened.append(argv[1])), \
                patch.object(server, "forward", side_effect=lambda port: {3000: 3000, 5173: 40001}.get(port)):
            for url in ("https://example.com/a?b=1", "http://localhost:3000/cb?code=x", "http://0.0.0.0:3000/",
                        "http://[::1]:5173/", "http://127.0.0.1:9/", "file:///etc/passwd", "javascript:alert(1)",
                        "ssh://localhost:22", "http://[bad"):
                server.open(url)
        self.assertEqual(opened, ["https://example.com/a?b=1", "http://localhost:3000/cb?code=x",
                                  "http://localhost:3000/", "http://[::1]:40001/", "http://127.0.0.1:9/"])

    def test_forward_prefers_the_same_port_and_relays_through_exec(self):
        with tempfile.TemporaryDirectory() as temp, echo_server() as target:
            # A docker that runs the exec here: it drops `exec -i <id>`.
            docker = Path(temp) / "docker"
            docker.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
            docker.chmod(0o755)
            server = helper.Server(str(docker), "abc", None)
            try:
                with socket.create_server(("127.0.0.1", 0)) as probe:
                    free = probe.getsockname()[1]
                self.assertEqual(server.forward(free), free)
                # The target holds its own port here, so the forward takes another one.
                port = target.getsockname()[1]
                server.ports(f"0100007F:{port:04X} 0100007F:{free:04X}")
                host_port = server.forwards[port][1]
                self.assertNotIn(host_port, (port, free))
                self.assertEqual(server.forward(port), host_port)
                with socket.create_connection(("127.0.0.1", host_port), timeout=5) as client:
                    client.sendall(b"through\n")
                    self.assertEqual(client.recv(8), b"THROUGH\n")
                    client.sendall(b"bye\n")
                    self.assertEqual(client.recv(8), b"")
                server.ports(f"0100007F:{free:04X}")
                self.assertEqual(list(server.forwards), [free])
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", host_port), timeout=1).close()
            finally:
                for listener, _ in server.forwards.values():
                    listener.close()

    def test_stop_ends_the_listener_and_run(self):
        server = helper.Server("docker", "abc", None)
        listener = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True)
        with patch.object(helper.subprocess, "Popen", return_value=listener) as popen:
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(50):
                if server.proc:
                    break
                time.sleep(0.05)
            server.stop()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(popen.call_count, 1)
        listener.stdin.close()

    def test_connecting_starts_serve_for_that_container_and_user(self):
        row = {"user": "vscode", "workspace": ""}
        with patch.object(helper, "serve_in_background") as serve, patch.object(helper.os, "execvpe"):
            helper.exec_container("docker", row, "abc", "true")
        serve.assert_called_once_with("docker", "abc", "vscode")


if __name__ == "__main__":
    unittest.main()
