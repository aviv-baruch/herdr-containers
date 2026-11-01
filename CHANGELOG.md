# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Daily rebuild workflow opens a PR when Herdr releases.
- Demo video (2x) and a full-window preview from neutral demo containers.
- README: how to add Containers to a layout you already use.

### Changed

- Clones rebuilt for Herdr 0.1.10.
- Published as a herdr.dev mod with topic `herdr-mod`; removed the
  `main-containers` layout.
- Ctrl+Shift+Y is now a global binding.

### Fixed

- Windows: the panel runs the helper through `cmd` and the new
  `requirements/windows` launchers (`hc-devcontainer.cmd` for the app, and an
  `hc-devcontainer` sh script for core's via under Git for Windows), since
  Windows can't start the Python script by name. Docker Desktop being down
  reads as "Docker isn't running". When core refuses `--via` on Windows,
  Connect and Repair say so.
- Dev containers made by Docker Desktop on Windows find their workspace, folder
  and machine name: the helper matches Windows folder labels to Docker
  Desktop's and WSL's mount paths, ignoring case.
- The helper on Windows runs `docker exec` as a child and passes its exit code;
  `os.exec*` there would end the connection at once.
- Terminals in containers start the user's passwd shell interactively instead
  of always using `/bin/sh`.
- Terminals in dev containers receive the config's `remoteEnv`, like VS Code.
- Spaces and tabs opened in a dev container without a folder start in its
  workspace instead of `/`. Repos cloned into a volume (VS Code's Clone
  Repository in Container Volume) are found too. A container whose Herdr Core
  is already running keeps the old folder until that core restarts: rebuild or
  restart the container, or run `hc machine update <machine>`.

## [0.1.0] - 2026-10-30

### Added

- `herdr-containers` layout package for Herdr app 0.1.9: Containers sidebar,
  four cloned mods, a layout key, and footer Settings → Containers controls.
- Dev container connect/start, plain container inline confirmation, Repair,
  Gone/Forget, and optimistic Disconnect.
- Resilience for stopped or deleted containers, Docker outages, host core
  restarts, and dev container rebuilds. The optional Herdr Core Feature
  1.1.0 preserves core state across rebuilds.
- `hc-devcontainer` host helper, package builder, drift test, and checks.
