"""Windows end to end: the app's cmd path and core's via path, as Herdr spawns them.

Runs only on Windows (a GitHub windows-latest runner). Docker there runs no Linux
containers, so fake_docker.py stands in and runs the container side in Git's sh.
With HERDR_CORE_BIN set, a patched herdr-core also adds a machine through the via.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GIT_BIN = Path(r"C:\Program Files\Git\bin")
FOLDER = r"c:\Users\Me\Bob's Proj & Co"
VIA = "hc-devcontainer 'c:\\Users\\Me\\Bob'\\''s Proj & Co'"
WORKSPACE = "/workspaces/Bob's Proj & Co"
# herdr-core machines.rs pipe(), Unix side, no session: the line core sends through a via.
CORE_LINE = ("sh -c 'p=$(command -v herdr-core) || p=$HOME/.local/bin/herdr-core; [ -x \"$p\" ] || "
             "{ echo \"herdr-core is not installed here\" >&2; exit 127; }; exec \"$p\" connect'")


def containers(exec_env=None):
    return [
        {"Id": "devid0001", "Name": "/bobs-dev", "Created": "2026-11-01T00:00:00Z",
         "State": {"Status": "running", "Running": True},
         "Config": {"Image": "vsc-bob", "WorkingDir": "/", "Env": [], "Labels": {
             "devcontainer.local_folder": FOLDER,
             "devcontainer.metadata": json.dumps([{"remoteUser": "me"}])}},
         # Docker Desktop's spelling of the drive, in another case than the label.
         "Mounts": [{"Type": "bind", "Source": "/run/desktop/mnt/host/c/users/me/Bob's Proj & Co",
                     "Destination": WORKSPACE}],
         "ExecEnv": exec_env or {}},
        {"Id": "webid0002", "Name": "/web", "Created": "2026-11-01T00:00:00Z",
         "State": {"Status": "running", "Running": True},
         "Config": {"Image": "nginx", "WorkingDir": "/tmp", "Env": [], "Labels": {}}, "Mounts": [],
         "ExecEnv": exec_env or {}},
        {"Id": "oldid0003", "Name": "/old", "Created": "2026-11-01T00:00:00Z",
         "State": {"Status": "exited", "Running": False},
         "Config": {"Image": "vsc-old", "WorkingDir": "/", "Env": [], "Labels": {
             "devcontainer.local_folder": r"C:\Users\Me\old"}}, "Mounts": []},
    ]


class Box:
    """A scratch install: the helper as hc-devcontainer.py beside both launchers, and the fakes."""

    def __init__(self, exec_env=None, core=None):
        self.dir = Path(tempfile.mkdtemp(prefix="hc-e2e-"))
        self.bin = self.dir / "bin"
        self.tools = self.dir / "tools"
        self.bin.mkdir()
        self.tools.mkdir()
        shutil.copy(ROOT / "requirements" / "hc-devcontainer", self.bin / "hc-devcontainer.py")
        for name in ("hc-devcontainer", "hc-devcontainer.cmd"):
            shutil.copy(ROOT / "requirements" / "windows" / name, self.bin / name)
        if core:
            for name in ("herdr-core.exe", "hc.exe"):
                shutil.copy(core, self.bin / name)
        subprocess.run(["rustc", "-O", "--edition", "2021", str(HERE / "harness.rs"),
                        "-o", str(self.tools / "harness.exe")], check=True)
        for role in ("app", "core", "docker"):
            shutil.copy(self.tools / "harness.exe", self.tools / f"{role}.exe")
        shutil.copy(HERE / "fake_docker.py", self.tools / "fake_docker.py")
        self.state = self.dir / "state.json"
        self.state.write_text(json.dumps(containers(exec_env)), encoding="utf-8")
        self.log = self.dir / "docker.log"
        self.log.write_text("", encoding="utf-8")
        sh = GIT_BIN / "sh.exe"
        # The workspace, as a container would have it, in Git's own root.
        subprocess.run([str(sh), "-c", 'mkdir -p "$1"', "sh", WORKSPACE], check=True)
        self.env = {**os.environ,
                    "PATH": os.pathsep.join([str(self.bin), str(GIT_BIN), os.environ["PATH"]]),
                    "HC_DEVCONTAINER_DOCKER": str(self.tools / "docker.exe"),
                    "FAKE_DOCKER_STATE": str(self.state), "FAKE_DOCKER_LOG": str(self.log),
                    "FAKE_DOCKER_SH": str(sh)}

    def app(self, *args, env=None):
        """The containers mod on Windows: exec("cmd", ["/d", "/c", "hc-devcontainer", ...])."""
        return subprocess.run([str(self.tools / "app.exe"), "cmd", "/d", "/c", "hc-devcontainer", *args],
                              env=env or self.env, capture_output=True, text=True, timeout=60)

    def core(self, via, line, stdin=""):
        return subprocess.run([str(self.tools / "core.exe"), via, line], env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=60)

    def calls(self, verb):
        return [c for c in map(json.loads, self.log.read_text(encoding="utf-8").splitlines()) if c[0] == verb]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipUnless(os.name == "nt", "Windows only")
class AppPath(unittest.TestCase):
    def setUp(self):
        self.box = Box()
        self.addCleanup(self.box.close)

    def test_ls_through_cmd_finds_the_workspace_from_docker_desktop_mounts(self):
        r = self.box.app("ls", "--all", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["id"]: row for row in json.loads(r.stdout)}
        self.assertEqual(set(rows), {"devid0001", "webid0002", "oldid0003"})
        dev = rows["devid0001"]
        self.assertEqual((dev["kind"], dev["folder"], dev["workspace"], dev["via"], dev["user"]),
                         ("devcontainer", FOLDER, WORKSPACE, VIA, "me"))
        self.assertEqual(rows["webid0002"]["via"], "hc-devcontainer --container 'web'")
        self.assertEqual(rows["oldid0003"]["state"], "exited")

    def test_exists_and_start_through_cmd(self):
        for via, code in ((VIA, 0), ("hc-devcontainer 'C:/USERS/ME/BOB'\\''S PROJ & CO'", 0),
                          ("hc-devcontainer --container 'web'", 0), ("hc-devcontainer 'c:\\gone'", 1),
                          ("hc-devcontainer --container 'gone'", 1)):
            r = self.box.app("exists", via)
            self.assertEqual(r.returncode, code, (via, r.stdout, r.stderr))
        r = self.box.app("start", "oldid0003")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.box.calls("start"), [["start", "oldid0003"]])

    def test_missing_helper_is_cmd_exit_9009(self):
        env = {**self.box.env, "PATH": os.pathsep.join(p for p in self.box.env["PATH"].split(os.pathsep)
                                                       if Path(p) != self.box.bin)}
        self.assertEqual(self.box.app("ls", "--all", "--json", env=env).returncode, 9009)


@unittest.skipUnless(os.name == "nt", "Windows only")
class ViaPath(unittest.TestCase):
    """Patched core runs a via as sh -c '<via> "$1"' sh <line> with Git's sh."""

    def setUp(self):
        self.box = Box()
        self.addCleanup(self.box.close)

    def test_folder_via_carries_stdin_stdout_workspace_and_exit_code(self):
        line = "printf 'pwd=%s\\n' \"$PWD\"; read x; printf 'got=%s\\n' \"$x\"; exit 7"
        r = self.box.core(VIA, line, stdin="ping\n")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn("got=ping", r.stdout.splitlines())
        self.assertTrue(r.stdout.splitlines()[0].endswith(WORKSPACE), r.stdout)
        execs = self.box.calls("exec")
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0][1:4], ["-i", "-u", "me"])
        self.assertEqual(execs[0][-2:], [line, WORKSPACE])
        self.assertIn("devid0001", execs[0])

    def test_line_reaches_docker_byte_for_byte(self):
        for line in (CORE_LINE, "/no/such/thing 2>/dev/null; echo ok", "echo \"a  b\" 'c\\d' %PATH% ^& |"):
            self.box.log.write_text("", encoding="utf-8")
            self.box.core(VIA, line)
            self.assertEqual(self.box.calls("exec")[0][-2], line)

    def test_core_line_runs_and_says_hc_is_missing(self):
        r = self.box.core(VIA, CORE_LINE)
        self.assertEqual(r.returncode, 127, (r.stdout, r.stderr))
        self.assertIn("herdr-core is not installed here", r.stderr)

    def test_plain_container_via(self):
        r = self.box.core("hc-devcontainer --container 'web'", "echo plain-ok; pwd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["plain-ok", "/tmp"])


@unittest.skipUnless(os.name == "nt" and os.environ.get("HERDR_CORE_BIN"), "needs a patched herdr-core on Windows")
class PatchedCore(unittest.TestCase):
    """A real hc machine add --via on Windows; the container side is a second core session."""

    def setUp(self):
        self.box = Box(exec_env={"HC_SESSION": "e2ebox"}, core=Path(os.environ["HERDR_CORE_BIN"]).resolve())
        self.addCleanup(self.box.close)
        self.addCleanup(self.stop)

    def hc(self, *args, session="e2ehost", timeout=120):
        return subprocess.run([str(self.box.bin / "hc.exe"), *args], env={**self.box.env, "HC_SESSION": session},
                              capture_output=True, text=True, timeout=timeout)

    def stop(self):
        for session in ("e2ehost", "e2ebox"):
            self.hc("server", "stop", session=session, timeout=60)

    def test_machine_add_via_connects(self):
        r = self.hc("machine", "add", "--name", "box", "--via", VIA, "--json")
        print("machine add:", r.returncode, r.stdout, r.stderr, sep="\n")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        state = None
        for _ in range(40):
            listed = self.hc("machine", "ls", "--json")
            machines = {m["name"]: m for m in json.loads(listed.stdout)["machines"]}
            state = machines.get("box", {}).get("state")
            if state == "connected":
                break
            time.sleep(0.5)
        print("machine ls:", listed.stdout, sep="\n")
        self.assertEqual(state, "connected")
        self.assertTrue(self.box.calls("exec"), "core never ran docker exec through the via")
        r = self.hc("exec", "--json", "--on", "box", "--timeout", "30", "hostname")
        print("exec on box:", r.returncode, r.stdout, r.stderr, sep="\n")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
