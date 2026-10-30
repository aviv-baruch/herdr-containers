# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
