"""A docker for the Windows runner: containers come from FAKE_DOCKER_STATE, every
call is logged to FAKE_DOCKER_LOG, and exec runs the container side in Git's sh."""

import json
import os
import subprocess
import sys

state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path, encoding="utf-8") as stream:
    state = json.load(stream)
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")


def find(ref):
    return next((c for c in state if ref in (c["Id"], c["Name"].lstrip("/"))), None)


if args[0] == "ps":
    rest = args[1:]
    labels = [rest[i + 1].split("=", 1)[1] for i, arg in enumerate(rest) if arg == "--filter"]
    for c in state:
        if ("-a" in rest or c["State"]["Status"] in ("running", "paused")) \
                and all(label in c["Config"]["Labels"] for label in labels):
            print(c["Id"])
elif args[0] == "inspect":
    found = []
    for ref in args[3:]:  # inspect --type container <ids>
        c = find(ref)
        if not c:
            print(f"Error: No such object: {ref}", file=sys.stderr)
            sys.exit(1)
        found.append(c)
    print(json.dumps(found))
elif args[0] == "start":
    find(args[1])["State"] = {"Status": "running", "Running": True}
    with open(state_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
    print(args[1])
elif args[0] == "exec":
    i = 1
    while args[i].startswith("-"):
        i += 2 if args[i] in ("-u", "-e") else 1
    c = find(args[i])
    if not c or c["State"]["Status"] != "running":
        print(f"Error: container {args[i]} is not running", file=sys.stderr)
        sys.exit(1)
    assert args[i + 1] == "sh", args
    # The container's own environment, e.g. its own HC_SESSION.
    env = {**os.environ, **c.get("ExecEnv", {})}
    sys.exit(subprocess.run([os.environ["FAKE_DOCKER_SH"], *args[i + 2:]], env=env, check=False).returncode)
else:
    print(f"fake docker: no {args[0]}", file=sys.stderr)
    sys.exit(1)
