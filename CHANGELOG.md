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

- Terminals in containers start the user's passwd shell interactively instead
  of always using `/bin/sh`.
- Terminals in dev containers receive the config's `remoteEnv`, like VS Code.
- Spaces and tabs opened in a dev container without a folder start in its
  workspace instead of `/`. Repos cloned into a volume (VS Code's Clone
  Repository in Container Volume) are found too. A container whose Herdr Core
  is already running keeps the old folder until that core restarts: rebuild or
  restart the container, or reconnect it with Repair.

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
