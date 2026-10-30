# Herdr Core Feature

Install Herdr Core in a Linux dev container and persist its core identity in a named Docker volume across rebuilds.

In a project beside this repository, copy `feature/herdr-core` into `.devcontainer/herdr-core`, then add:

```json
{
  "features": {
    "./herdr-core": { "version": "latest" }
  }
}
```

The Feature installs the binary at the remote user's `~/.local/bin/herdr-core`, where `hc machine update` (and the update `hc machine add` offers) replaces it, and links `/usr/local/bin/herdr-core` to it, because `docker exec`'s PATH has no `~/.local/bin`. It adds `/usr/local/bin/hc` only if no `hc` already exists. `version` can be a published release such as `0.2.0`. `latest` is resolved when the image builds, and Docker caches that layer: pin a version, or rebuild without the cache, to move it. It supports Linux x86_64 and aarch64, with `apt-get` or `apk` to supply curl and tar when needed. It installs after `common-utils`, which often creates the remote user.

The volume `herdr-core-state-${devcontainerId}` is mounted at `/var/lib/herdr-core-state`. Herdr's default state path under the remote user's home is a symlink to it. The mount directory is world-writable and sticky, like `/tmp`, so a new volume works even after the devcontainers CLI remaps the user's UID; hc creates its session folder inside it with mode 0700. The Dev Container spec defines `devcontainerId` as stable across rebuilds. Keep the same workspace identity and volume; deleting the volume creates a new core identity. If a container already has nonempty `~/.local/state/herdr-core`, move that state before adding this Feature.

The core also takes a new identity when `/etc/machine-id` changes, as on a cloned disk. Images without one, or with one that stays the same across builds, keep their id; the install warns when the image has one.

After publication, use the OCI Feature ID instead:

```json
{
  "features": {
    "ghcr.io/<owner>/<features-repository>/herdr-core:1": { "version": "latest" }
  }
}
```

This repo does not publish an OCI Feature yet.
