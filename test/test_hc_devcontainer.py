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
# The docker exec fallback, whatever this machine has; CliTests turn the Dev Containers CLI on.
os.environ["HC_DEVCONTAINER_CLI"] = ""


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


class CliTests(unittest.TestCase):
    """The Dev Containers CLI path: VS Code's own open and attach sequence."""

    # Where run-user-commands stops for each (blocking, stop) phase, by waitFor.
    PHASES = {
        "updateContentCommand": {(True, True): "skipNonBlocking", (False, True): "stopForPersonalization",
                                 (False, False): "done"},
        "postStartCommand": {(True, True): "stopForPersonalization", (True, False): "skipNonBlocking",
                             (False, False): "done"},
    }

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.folder = self.temp / "My Project"
        (self.folder / ".devcontainer").mkdir(parents=True)
        self.config = self.folder / ".devcontainer" / "devcontainer.json"
        self.config.write_text("{}")
        self.home = self.temp / "host-home"
        (self.home / ".ssh").mkdir(parents=True)
        (self.home / ".ssh" / "known_hosts").write_text("github.com ssh-ed25519 AAAA\n")
        (self.home / ".gitconfig").write_text("[user]\n\tname = Me\n")
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".config" / "git" / "config").write_text("[core]\n")
        self.vscode = self.temp / "vscode-user"
        self.vscode.mkdir()
        self.settings = self.vscode / "settings.json"
        self.dev = {
            "Id": "dev-id", "Name": "/dev-work", "Created": "2026-10-30",
            "State": {"Status": "running", "Running": True},
            "Config": {"Labels": {"devcontainer.local_folder": str(self.folder),
                                  "devcontainer.config_file": str(self.config),
                                  "devcontainer.metadata": '[{"remoteUser":"vscode"}]'},
                       "WorkingDir": "/fallback", "Image": "dev:latest"},
            "Mounts": [{"Source": str(self.folder), "Destination": "/workspaces/project"}],
        }
        env = patch.dict(os.environ, {"HC_DEVCONTAINER_CLI": "devcontainer", "HOME": str(self.home),
                                      "XDG_STATE_HOME": str(self.temp / "state"),
                                      "HC_DEVCONTAINER_VSCODE_USER": str(self.vscode)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)
        which = patch.object(helper.shutil, "which", side_effect=lambda name: f"/bin/{name}")
        which.start()
        self.addCleanup(which.stop)
        self.target = ["--workspace-folder", str(self.folder), "--config", str(self.config),
                       "--id-label", f"devcontainer.local_folder={self.folder}",
                       "--id-label", f"devcontainer.config_file={self.config}"]

    def connect(self, argv, container=None, wait_for="updateContentCommand", up_code=0, copies=(),
                gh_needs_login=False):
        """Run a connection against fakes; returns the steps in order, '|' where the child forks."""
        steps, calls = [], []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[:2] == ["devcontainer", "up"]:
                steps.append("up")
                return done(b'{"outcome":"success","remoteUser":"vscode"}\n', code=up_code)
            if cmd[:2] == ["devcontainer", "run-user-commands"]:
                key = ("--skip-non-blocking-commands" in cmd, "--stop-for-personalization" in cmd)
                steps.append(f"hooks{key}")
                return done(json.dumps({"outcome": "success", "result": self.PHASES[wait_for][key]}).encode())
            if cmd[:2] == ["devcontainer", "exec"]:
                steps.append("gh-login" if "--with-token" in cmd[-1] else "gh-check")
                return done(code=0 if gh_needs_login else 1)
            if cmd[:2] == ["gh", "auth"]:
                steps.append("host-token")
                return done(b"gho_token\n")
            if cmd[:3] == ["git", "config", "--global"]:
                return done(code=1)
            if cmd[1] == "exec" and 'printf %s "$HOME"' in cmd:
                return done(b"/home/vscode")
            if cmd[1] == "exec":
                script = cmd[cmd.index("-c") + 1]
                name = "cleanup" if script == helper.GIT_CLEANUP else f"copy {cmd[-2]}"
                steps.append(name)
                return done(code=0 if name in copies else 1)
            raise AssertionError(cmd)

        with patch.object(helper, "inspect", return_value=[container or self.dev]), \
                patch.object(helper, "folder_ids", return_value=["dev-id"]), \
                patch.object(helper.subprocess, "run", side_effect=run), \
                patch.object(helper, "detach", side_effect=lambda work, log: (steps.append("|"), work())), \
                patch.object(helper, "serve_in_background") as serve, \
                patch.object(helper.os, "execvp") as execvp, \
                patch.object(helper.os, "execvpe") as execvpe, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(helper.main(argv), 0)
        return SimpleNamespace(steps=steps, calls=calls, execvp=execvp, execvpe=execvpe, serve=serve,
                               errors=errors.getvalue())

    def engine(self, bin_dir):
        """VS Code's record of its engine, as the extension writes it on activation."""
        electron, extension = self.temp / "code" / "code", self.temp / "ext"
        electron.parent.mkdir(exist_ok=True)
        electron.write_text("#!/bin/sh\n")
        electron.chmod(0o755)
        script = extension / "dist" / "spec-node" / "devContainersSpecCLI.js"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("")
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "vscode-path").write_text(str(electron))
        (bin_dir / "remote-containers-path").write_text(str(extension))
        return [str(electron), str(script)]

    def test_cli_comes_from_the_environment(self):
        with patch.object(helper.shutil, "which", side_effect=lambda name: f"/bin/{name}" if name != "missing" else None):
            for value, expected in (("devcontainer", ["devcontainer"]),
                                    ("node '/x/my cli.js'", ["node", "/x/my cli.js"]),
                                    ("", None), ("missing --flag", None)):
                with self.subTest(value=value), patch.dict(os.environ, {"HC_DEVCONTAINER_CLI": value}):
                    self.assertEqual(helper.devcontainer_cli(), expected)
            # cmd would re-parse the connection line through devcontainer.cmd.
            with patch.object(helper, "WINDOWS", True):
                self.assertIsNone(helper.devcontainer_cli())

    def test_vs_codes_own_engine_comes_before_a_devcontainer_command(self):
        del os.environ["HC_DEVCONTAINER_CLI"]
        os.environ.pop("ELECTRON_RUN_AS_NODE", None)
        with patch.object(helper.shutil, "which", return_value=None):
            self.assertIsNone(helper.devcontainer_cli())
        with patch.object(helper.shutil, "which", return_value="/usr/bin/devcontainer"):
            self.assertEqual(helper.devcontainer_cli(), ["devcontainer"])
            self.assertNotIn("ELECTRON_RUN_AS_NODE", os.environ)
            engine = self.engine(self.vscode / "globalStorage" / "ms-vscode-remote.remote-containers" / "cli-bin")
            self.assertEqual(helper.devcontainer_cli(), engine)
        self.assertEqual(os.environ["ELECTRON_RUN_AS_NODE"], "1")

    def test_vs_codes_devcontainer_command_is_never_run_itself(self):
        # It has only open and build; the files beside it name the engine.
        del os.environ["HC_DEVCONTAINER_CLI"]
        shim = self.temp / "cli-bin" / "devcontainer"
        with patch.object(helper.shutil, "which", return_value=str(shim)):
            shim.parent.mkdir()
            (shim.parent / "vscode-path").write_text(str(self.temp / "gone"))
            self.assertIsNone(helper.devcontainer_cli())
            engine = self.engine(shim.parent)
            self.assertEqual(helper.devcontainer_cli(), engine)

    def test_default_wait_for_connects_after_update_content_and_personalizes_in_the_background(self):
        got = self.connect([str(self.folder), "true"])
        known_hosts, gitconfig = "/home/vscode/.ssh/known_hosts", "/home/vscode/.gitconfig"
        self.assertEqual(got.steps, [
            "up", f"copy {known_hosts}", "hooks(True, True)", "|",
            "gh-check", "hooks(False, True)", f"copy {gitconfig}", "copy /home/vscode/.config/git/config",
            "hooks(False, False)"])
        got.execvp.assert_called_once()

    def test_wait_for_past_post_create_personalizes_before_connecting(self):
        got = self.connect([str(self.folder), "true"], wait_for="postStartCommand")
        self.assertEqual(got.steps, [
            "up", "copy /home/vscode/.ssh/known_hosts", "hooks(True, True)", "copy /home/vscode/.gitconfig",
            "copy /home/vscode/.config/git/config", "hooks(True, False)", "|", "gh-check", "hooks(False, False)"])

    def test_cli_calls_name_the_container_its_config_and_vs_code_settings(self):
        self.settings.write_text("""{
            // VS Code writes comments and trailing commas
            "dotfiles.repository": "https://example.com/me/dotfiles.git", /* a URL holds // */
            "dotfiles.targetPath": "~/dots",
            "dotfiles.installCommand": "",
        }""")
        with patch.dict(os.environ, {"HC_DEVCONTAINER_DOCKER": "podman"}):
            got = self.connect([str(self.folder), "printf hi"])
        common = ["--docker-path", "podman"]
        up = next(cmd for cmd, _ in got.calls if cmd[1] == "up")
        self.assertEqual(up, ["devcontainer", "up", *common, *self.target, "--expect-existing-container",
                              "--skip-post-create"])
        hooks = [cmd for cmd, _ in got.calls if cmd[1] == "run-user-commands"]
        session = hooks[0][hooks[0].index("--container-session-data-folder") + 1]
        self.assertRegex(session, r"^/tmp/devcontainers-[0-9a-f-]{36}$")
        self.assertEqual(hooks[0][:-1], [
            "devcontainer", "run-user-commands", *common, "--container-id", "dev-id", *self.target,
            "--remote-env", "REMOTE_CONTAINERS=true", "--container-session-data-folder", session,
            "--dotfiles-repository", "https://example.com/me/dotfiles.git", "--dotfiles-target-path", "~/dots",
            "--skip-non-blocking-commands"])
        self.assertTrue(all(session in cmd for cmd in hooks))
        # The CLI sets the user, userEnvProbe env, remoteEnv and the workspace folder itself.
        got.execvp.assert_called_once_with("devcontainer", [
            "devcontainer", "exec", *common, "--container-id", "dev-id", *self.target,
            "--remote-env", "REMOTE_CONTAINERS=true", "sh", "-c", helper.WRAPPER, "sh", "printf hi"])
        # Links and ports are served as the remoteUser up reported.
        got.serve.assert_called_once_with("podman", "dev-id", "vscode")
        # Copies run as the remoteUser up reported, with the host file on stdin.
        copy = next((cmd, kw) for cmd, kw in got.calls if cmd[:2] == ["podman", "exec"] and cmd[-1] == "ssh")
        self.assertEqual(copy[0][:6], ["podman", "exec", "-i", "-u", "vscode", "dev-id"])
        self.assertEqual(copy[1]["input"], b"github.com ssh-ed25519 AAAA\n")
        log = self.temp / "state" / "hc-devcontainer" / "dev-work.log"
        self.assertIn(b"remoteUser", log.read_bytes())

    def test_copied_git_config_is_cleaned_up_and_settings_can_turn_copying_off(self):
        got = self.connect([str(self.folder), "true"], copies=("copy /home/vscode/.gitconfig",))
        self.assertIn("cleanup", got.steps)
        gitconfig = next(kw for cmd, kw in got.calls if cmd[-2:] == ["/home/vscode/.gitconfig", "git"])
        self.assertEqual(gitconfig["input"], b"[user]\n\tname = Me\n")
        self.settings.write_text('{"dev.containers.copyGitConfig": false}')
        got = self.connect([str(self.folder), "true"])
        self.assertFalse([step for step in got.steps if "gitconfig" in step or "git/config" in step])

    def test_gh_logs_in_with_the_host_token_only_where_it_is_logged_out(self):
        got = self.connect([str(self.folder), "true"], gh_needs_login=True)
        self.assertEqual(got.steps[got.steps.index("|") + 1:][:3], ["gh-check", "host-token", "gh-login"])
        login = next(kw for cmd, kw in got.calls if cmd[-1] == "gh auth login --with-token")
        self.assertEqual(login["input"], b"gho_token")
        self.settings.write_text('{"dev.containers.githubCLILoginWithToken": false}')
        got = self.connect([str(self.folder), "true"], gh_needs_login=True)
        self.assertNotIn("gh-check", got.steps)

    def test_failed_up_still_connects_by_container_id(self):
        got = self.connect([str(self.folder), "true"], up_code=1)
        self.assertFalse([step for step in got.steps if step.startswith("hooks")])
        self.assertIn("copy /home/vscode/.gitconfig", got.steps)
        got.execvp.assert_called_once_with("devcontainer", [
            "devcontainer", "exec", "--docker-path", "docker", "--container-id", "dev-id",
            "--remote-env", "REMOTE_CONTAINERS=true", "sh", "-c", helper.WRAPPER, "sh", "true", "/workspaces/project"])
        self.assertIn("lifecycle commands skipped", got.errors)

    def test_a_cli_that_cannot_run_falls_back_to_docker_exec(self):
        # e.g. the npm shim's `env node` without node on core's PATH.
        got = self.connect([str(self.folder), "true"], up_code=127)
        self.assertEqual(got.steps, ["up"])
        got.execvp.assert_not_called()
        self.assertEqual(got.execvpe.call_args.args[1][:2], ["docker", "exec"])
        self.assertIn("using docker exec", got.errors)

    def test_without_the_config_on_this_host_there_are_no_lifecycle_commands(self):
        self.config.unlink()
        got = self.connect([str(self.folder), "true"])
        self.assertFalse([step for step in got.steps if step in ("up",) or step.startswith("hooks")])
        self.assertEqual(got.execvp.call_args.args[1][-3:], ["sh", "true", "/workspaces/project"])

    def test_legacy_label_execs_by_container_id_without_lifecycle(self):
        legacy = copy.deepcopy(self.dev)
        legacy["Config"]["Labels"] = {"vsch.local.folder": str(self.folder)}
        got = self.connect([str(self.folder), "true"], container=legacy)
        self.assertNotIn("up", got.steps)
        self.assertNotIn("--workspace-folder", got.execvp.call_args.args[1])

    def test_plain_container_attaches_through_the_cli(self):
        plain = {"Id": "plain-id", "Name": "/web", "State": {"Status": "running"},
                 "Config": {"Labels": {}, "WorkingDir": "/app", "User": "app"}}
        got = self.connect(["--container", "web", "true"], container=plain)
        self.assertEqual(got.steps, ["copy /home/vscode/.ssh/known_hosts", "copy /home/vscode/.gitconfig",
                                     "copy /home/vscode/.config/git/config", "|", "gh-check"])
        got.execvp.assert_called_once_with("devcontainer", [
            "devcontainer", "exec", "--docker-path", "docker", "--container-id", "web",
            "--remote-env", "REMOTE_CONTAINERS=true", "sh", "-c", helper.WRAPPER, "sh", "true", "/app"])
        got.serve.assert_called_once_with("docker", "web", "app")

    def test_start_opens_folder_dev_containers_like_vs_code(self):
        stopped = copy.deepcopy(self.dev)
        stopped["State"] = {"Status": "exited", "Running": False}
        with patch.object(helper, "inspect", return_value=[stopped]), \
                patch.object(helper.subprocess, "run", return_value=done()) as run:
            self.assertEqual(helper.main(["start", "dev-id"]), 0)
        run.assert_called_once_with(
            ["devcontainer", "up", "--docker-path", "docker", *self.target,
             "--expect-existing-container", "--skip-post-create"],
            stdin=helper.subprocess.DEVNULL, capture_output=True, check=False)

    def test_start_falls_back_to_docker_start(self):
        stopped = copy.deepcopy(self.dev)
        failed = done(code=1, stdout=b'{"outcome":"error","message":"bad config"}', stderr=b"")
        with patch.object(helper, "inspect", return_value=[stopped]), \
                patch.object(helper.subprocess, "run", side_effect=[failed, done()]) as run, \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(helper.main(["start", "dev-id"]), 0)
        self.assertEqual(run.call_args.args[0], ["docker", "start", "dev-id"])
        self.assertIn("bad config", errors.getvalue())
        stopped["Config"]["Labels"] = {}
        with patch.object(helper, "inspect", return_value=[stopped]), \
                patch.object(helper.subprocess, "run", return_value=done()) as run:
            self.assertEqual(helper.main(["start", "dev-id"]), 0)
        run.assert_called_once_with(["docker", "start", "dev-id"], capture_output=True, text=True, check=False)

    def test_ls_json_keeps_its_fields(self):
        with patch.object(helper, "inspect", return_value=[self.dev]), patch.object(helper, "ps", return_value=[]):
            with redirect_stdout(io.StringIO()) as output:
                helper.main(["ls", "--all", "--json"])
        self.assertNotIn("cli_labels", json.loads(output.getvalue())[0])


class VsCodeScriptTests(unittest.TestCase):
    """VS Code's in-container snippets, run by a real sh."""

    def copy(self, target, needs="sh", in_use="", data=b"host\n"):
        script = helper.COPY.format(in_use=in_use)
        return subprocess.run(["sh", "-c", script, "sh", str(target), needs], input=data, check=False).returncode

    def test_copy_only_where_none_is_and_the_reader_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "new dir" / "file"
            self.assertEqual(self.copy(target), 0)
            self.assertEqual(target.read_bytes(), b"host\n")
            self.assertEqual(self.copy(target, data=b"again\n"), 1)
            self.assertEqual(target.read_bytes(), b"host\n")
            self.assertEqual(self.copy(Path(temp) / "other", needs="no-such-command-here"), 1)
            self.assertFalse((Path(temp) / "other").exists())

    def test_gitconfig_with_only_filter_or_safe_sections_gets_the_host_appended(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".gitconfig"
            target.write_text("[safe]\n\tdirectory = *\n[filter \"lfs\"]\n\tclean = x\n")
            self.assertEqual(self.copy(target, in_use=helper.GITCONFIG_IN_USE), 0)
            self.assertTrue(target.read_text().endswith("host\n"))
            target.write_text("[user]\n\tname = Them\n")
            self.assertEqual(self.copy(target, in_use=helper.GITCONFIG_IN_USE), 1)
            self.assertEqual(target.read_text(), "[user]\n\tname = Them\n")

    def test_settings_jsonc(self):
        text = '{ // c\n "a": "x // not a comment, }", /* block */ "b": [1, 2,], "c": "q\\"//",\n}'
        self.assertEqual(json.loads(helper.jsonc(text)), {"a": "x // not a comment, }", "b": [1, 2], "c": 'q"//'})

    def test_detached_work_holds_none_of_the_connection_pipes(self):
        # The parent returns at once and core's stdout closes when it exits, while the child works on.
        with tempfile.TemporaryDirectory() as temp:
            marker, log = Path(temp) / "marker", Path(temp) / "log"
            code = (f"import importlib.machinery, importlib.util, sys, time\n"
                    f"loader = importlib.machinery.SourceFileLoader('h', {str(path)!r})\n"
                    f"spec = importlib.util.spec_from_loader('h', loader); h = importlib.util.module_from_spec(spec)\n"
                    f"loader.exec_module(h)\n"
                    f"def work():\n    time.sleep(0.5); print('child'); open({str(marker)!r}, 'w').close()\n"
                    f"h.detach(work, open({str(log)!r}, 'wb', buffering=0))\n"
                    f"print('parent')\n")
            started = __import__("time").monotonic()
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=5)
            elapsed = __import__("time").monotonic() - started
            self.assertEqual(result.stdout, "parent\n")
            self.assertLess(elapsed, 0.45)
            for _ in range(50):
                if marker.exists():
                    break
                __import__("time").sleep(0.05)
            self.assertTrue(marker.exists())
            self.assertEqual(log.read_text(), "child\n")

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
                # Not communicate(): before 3.13 it flushes the closed stdin and raises.
                proc.kill()
                proc.wait()
                proc.stdout.close()
                if not proc.stdin.closed:
                    proc.stdin.close()
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
        # exec: dash forks a plain `sleep`, which would keep stdout open after the kill.
        listener = subprocess.Popen(["/bin/sh", "-c", "exec sleep 30"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True)
        with patch.object(helper.subprocess, "Popen", return_value=listener) as popen, \
                patch.object(server, "setup_credentials"):
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


def fake_listener(home):
    """A live `serve` listener as the container helpers see it: its pid, marked, in the lock."""
    proc = subprocess.Popen(["/bin/sh", "-c", "# hc-devcontainer-serve\nsleep 30"])
    folder = Path(home) / ".cache" / "herdr-containers"
    (folder / "serve.lock").mkdir(parents=True)
    (folder / "cred").mkdir()
    (folder / "serve.lock" / "pid").write_text(f"{proc.pid}\n")
    return proc, folder


class CredentialTests(unittest.TestCase):
    """The git credential bridge: container git asks the host's git, as with VS Code."""

    def helper_file(self, folder):
        path = folder / "git-credential"
        path.write_text(helper.CREDENTIAL_HELPER)
        path.chmod(0o755)
        return path

    def test_helper_is_silent_without_a_listener(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".cache" / "herdr-containers"
            folder.mkdir(parents=True)
            started = time.monotonic()
            result = subprocess.run([str(self.helper_file(folder)), "get"], input=b"protocol=https\nhost=a.test\n",
                                    capture_output=True, timeout=5, check=False)
            self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((result.returncode, result.stdout), (0, b""))

    def test_helper_asks_the_host_and_prints_its_answer(self):
        with tempfile.TemporaryDirectory() as home:
            listener, folder = fake_listener(home)
            try:
                helper_path = self.helper_file(folder)
                answered = []

                def host():  # what serve does with a request
                    for _ in range(100):
                        found = list((folder / "cred").glob("*.req"))
                        if found:
                            request = found[0]
                            answered.append(request.read_bytes())
                            request.unlink()
                            run_script(helper.CREDENTIAL_ANSWER, request.stem, env={**os.environ, "HOME": home},
                                       input="username=me\npassword=tok\n")
                            return
                        time.sleep(0.05)

                thread = threading.Thread(target=host)
                thread.start()
                got = subprocess.run([str(helper_path), "get"], input=b"protocol=https\nhost=a.test\n",
                                     capture_output=True, timeout=10, check=False)
                thread.join(5)
                self.assertEqual(got.stdout, b"username=me\npassword=tok\n")
                self.assertEqual(answered, [b"get\nprotocol=https\nhost=a.test\n"])
                self.assertEqual(list((folder / "cred").iterdir()), [])
                # store and erase hand the request over and return at once.
                started = time.monotonic()
                stored = subprocess.run([str(helper_path), "store"], input=b"protocol=https\nhost=a.test\n",
                                        capture_output=True, timeout=5, check=False)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(stored.returncode, 0)
                request = next((folder / "cred").glob("*.req"))
                self.assertEqual(request.read_bytes(), b"store\nprotocol=https\nhost=a.test\n")
                self.assertEqual(request.stat().st_mode & 0o077, 0)
                # Anything but git's three operations is ignored.
                self.assertEqual(subprocess.run([str(helper_path), "list"], input=b"", check=False).returncode, 0)
                self.assertEqual(len(list((folder / "cred").glob("*.req"))), 1)
            finally:
                listener.kill()
                listener.wait()

    def test_listener_hands_over_requests_and_drops_stale_ones(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".cache" / "herdr-containers"
            (folder / "cred").mkdir(parents=True)
            (folder / "cred" / "1-1.req").write_text("get\nstale\n")
            proc = subprocess.Popen(["/bin/sh", "-c", helper.LISTEN], env={**os.environ, "HOME": home},
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertTrue(proc.stdout.readline().startswith("ports "))
                self.assertEqual(list((folder / "cred").iterdir()), [])
                (folder / "cred" / "12-34.req").write_bytes(b"get\nprotocol=https\nhost=a.test\n")
                line = proc.stdout.readline()
                kind, request_id, encoded = line.rstrip("\n").split(" ")
                self.assertEqual((kind, request_id), ("cred", "12-34"))
                self.assertEqual(bytes.fromhex(encoded), b"get\nprotocol=https\nhost=a.test\n")
                for _ in range(40):  # it removes the request right after printing it
                    if not list((folder / "cred").iterdir()):
                        break
                    time.sleep(0.05)
                self.assertEqual(list((folder / "cred").iterdir()), [])
            finally:
                proc.kill()
                proc.wait()
                proc.stdout.close()
                proc.stdin.close()

    def test_server_answers_get_with_the_hosts_git_and_forwards_store_and_erase(self):
        server = helper.Server("docker", "abc", "vscode")
        request = b"get\nprotocol=https\nhost=a.test\nBAD KEY=x\nnot a pair\nwwwauth[]=Basic\n"
        with patch.object(helper.shutil, "which", return_value="/usr/bin/git"), \
                patch.object(helper.subprocess, "run", return_value=done(b"username=me\npassword=tok\n")) as run:
            server.credential("12-34", request.hex())
        fill, answer = run.call_args_list
        self.assertEqual(fill.args[0], ["git", "credential", "fill"])
        self.assertEqual(fill.kwargs["input"], b"protocol=https\nhost=a.test\nwwwauth[]=Basic\n\n")
        self.assertEqual(fill.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(answer.args[0], server.exec_argv(helper.CREDENTIAL_ANSWER, "12-34"))
        self.assertEqual(answer.kwargs["input"], b"username=me\npassword=tok\n")
        # A failed fill still answers, empty, so the container's git moves on.
        with patch.object(helper.shutil, "which", return_value="/usr/bin/git"), \
                patch.object(helper.subprocess, "run", return_value=done(code=128, stderr=b"")) as run:
            server.credential("12-34", request.hex())
        self.assertEqual(run.call_args.kwargs["input"], b"")
        self.assertEqual(run.call_args.args[0][-1], "exit 128")
        for op, action in (("store", "approve"), ("erase", "reject")):
            with patch.object(helper.shutil, "which", return_value="/usr/bin/git"), \
                    patch.object(helper.subprocess, "run", return_value=done()) as run:
                server.credential("12-34", f"{op}\nprotocol=https\nhost=a.test\nusername=me\npassword=tok\n".encode().hex())
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["git", "credential", action])
        for request_id, encoded in (("12-34", b"list\nhost=a\n".hex()), ("../x", b"get\nhost=a\n".hex()),
                                    ("12-34", "not hex")):
            with patch.object(helper.subprocess, "run") as run:
                server.credential(request_id, encoded)
            run.assert_not_called()

    def test_the_apps_desktop_session_reaches_host_git_and_links(self):
        # Core may run outside the desktop session; GCM shows its window only with a display.
        with tempfile.TemporaryDirectory() as state:
            app = {"XDG_STATE_HOME": state, "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
                   "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "OTHER": "not kept"}
            with patch.dict(os.environ, app), patch.object(helper, "inspect", return_value=[]), \
                    patch.object(helper, "ps", return_value=[]), redirect_stdout(io.StringIO()):
                for name in set(helper.DESKTOP_VARS) - set(app):  # this machine's own
                    os.environ.pop(name, None)
                helper.main(["ls", "--json"])
            saved = Path(state) / "hc-devcontainer" / "desktop.json"
            self.assertEqual(json.loads(saved.read_text()), {
                "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"})
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            # A run without a display (core's, or an ssh shell) keeps the record.
            core = {"XDG_STATE_HOME": state, "DBUS_SESSION_BUS_ADDRESS": "unix:path=/own"}
            with patch.dict(os.environ, core):
                for name in set(helper.DESKTOP_VARS) - set(core):
                    os.environ.pop(name, None)
                with patch.object(helper, "inspect", return_value=[]), patch.object(helper, "ps", return_value=[]), \
                        redirect_stdout(io.StringIO()):
                    helper.main(["ls", "--json"])
                env = helper.host_env()
                self.assertEqual((env["DISPLAY"], env["WAYLAND_DISPLAY"]), (":0", "wayland-0"))
                self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/own")  # its own wins
                self.assertNotIn("OTHER", env)
                server = helper.Server("docker", "abc", None)
                with patch.object(helper.shutil, "which", return_value="/usr/bin/git"), \
                        patch.object(helper.subprocess, "run", return_value=done(b"password=x\n")) as run:
                    server.credential("1-2", b"get\nprotocol=https\nhost=a.test\n".hex())
                self.assertEqual(run.call_args_list[0].kwargs["env"]["DISPLAY"], ":0")
                with patch.object(helper.subprocess, "Popen") as popen:
                    server.open("https://example.test/")
                self.assertEqual(popen.call_args.kwargs["env"]["WAYLAND_DISPLAY"], "wayland-0")

    def test_why_the_host_could_not_answer_is_shown_in_the_container(self):
        server = helper.Server("docker", "abc", None)
        failed = done(code=1, stderr=b"fatal: Cannot prompt because\n  terminal prompts have been disabled.\x1b[0m\n")
        with patch.object(helper.shutil, "which", return_value="/usr/bin/git"), \
                patch.object(helper.subprocess, "run", return_value=failed) as run:
            server.credential("1-2", b"get\nprotocol=https\nhost=a.test\n".hex())
        message = "fatal: Cannot prompt because terminal prompts have been disabled.[0m"
        self.assertEqual(run.call_args.args[0], server.exec_argv(helper.CREDENTIAL_ANSWER, "1-2", message))
        with patch.object(helper.shutil, "which", return_value=None), patch.object(helper.subprocess, "run") as run:
            server.credential("1-2", b"get\nprotocol=https\nhost=a.test\n".hex())
        self.assertIn("git is not installed on the host", run.call_args.args[0][-1])
        # The container's helper prints it where git ran, then git moves on.
        with tempfile.TemporaryDirectory() as home:
            listener, folder = fake_listener(home)
            try:
                path = self.helper_file(folder)

                def host():
                    for _ in range(100):
                        found = list((folder / "cred").glob("*.req"))
                        if found:
                            found[0].unlink()
                            run_script(helper.CREDENTIAL_ANSWER, found[0].stem, "fatal: no display",
                                       env={**os.environ, "HOME": home}, input="")
                            return
                        time.sleep(0.05)

                thread = threading.Thread(target=host)
                thread.start()
                got = subprocess.run([str(path), "get"], input=b"protocol=https\nhost=a.test\n",
                                     capture_output=True, timeout=10, check=False)
                thread.join(5)
            finally:
                listener.kill()
                listener.wait()
            self.assertEqual((got.returncode, got.stdout), (0, b""))
            self.assertEqual(got.stderr, b"herdr-containers: the host's git could not answer: fatal: no display\n")
            self.assertEqual(list((folder / "cred").iterdir()), [])

    def test_setup_follows_vs_codes_setting(self):
        server = helper.Server("docker", "abc", "vscode")
        for where, expected in ((None, [("global", "vscode")]), ("global", [("global", "vscode")]),
                                ("system", [("", "vscode"), ("system", "root")]), ("none", [])):
            settings = {} if where is None else {"dev.containers.gitCredentialHelperConfigLocation": where}
            with self.subTest(where=where), patch.object(helper, "vscode_settings", return_value=settings), \
                    patch.object(helper.subprocess, "run", return_value=done()) as run:
                server.setup_credentials()
            got = [(call.args[0][-2], call.args[0][4]) for call in run.call_args_list]
            self.assertEqual(got, expected)
            if expected:
                self.assertEqual(run.call_args_list[0].kwargs["input"], helper.CREDENTIAL_HELPER.encode())

    def test_setup_installs_a_private_helper_and_configures_git_once(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run(["git", "config", "--global", "--add", "credential.helper", "cache"], env=env, check=True)
            for _ in range(2):
                result = run_script(helper.CREDENTIAL_SETUP, "global", helper.GIT_CREDENTIAL, env=env,
                                    input=helper.CREDENTIAL_HELPER)
                self.assertEqual(result.returncode, 0, result.stderr)
            folder = Path(home) / ".cache" / "herdr-containers"
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual((folder / "git-credential").stat().st_mode & 0o777, 0o755)
            self.assertEqual((folder / "git-credential").read_text(), helper.CREDENTIAL_HELPER)
            helpers = subprocess.run(["git", "config", "--global", "--get-all", "credential.helper"], env=env,
                                     capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(helpers, ["cache", helper.GIT_CREDENTIAL])

    def test_container_git_gets_the_hosts_credentials_end_to_end(self):
        # Real git on both sides; a fake docker runs the "container" here with its own HOME.
        with tempfile.TemporaryDirectory() as temp:
            host_home, container_home = Path(temp) / "host", Path(temp) / "container"
            host_home.mkdir()
            container_home.mkdir()
            (host_home / ".git-credentials").write_text("https://me:tok@example.test\n")
            git_env = {"GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run(["git", "config", "--global", "credential.helper", "store"],
                           env={**os.environ, **git_env, "HOME": str(host_home)}, check=True)
            docker = Path(temp) / "docker"
            docker.write_text('#!/bin/sh\nshift\nwhile :; do case "$1" in -i) shift ;; -u) shift 2 ;; *) break ;; esac; done\n'
                              f'shift\nHOME={shlex.quote(str(container_home))} exec "$@"\n')
            docker.chmod(0o755)
            server = helper.Server(str(docker), "abc", None)
            with patch.dict(os.environ, {**git_env, "HOME": str(host_home)}), \
                    patch.object(helper, "vscode_settings", return_value={}), patch.object(server, "ports"):
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()
                try:
                    lock = container_home / ".cache" / "herdr-containers" / "serve.lock" / "pid"
                    for _ in range(100):
                        if lock.exists() and (lock.parent.parent / "cred").exists():
                            break
                        time.sleep(0.05)
                    got = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=example.test\n\n",
                                         env={**os.environ, "HOME": str(container_home), "GIT_TERMINAL_PROMPT": "0"},
                                         capture_output=True, text=True, timeout=20, check=False)
                finally:
                    server.stop()
                    thread.join(5)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("username=me\npassword=tok\n", got.stdout)


if __name__ == "__main__":
    unittest.main()
