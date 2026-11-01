# herdr-containers

This repo is the Herdr mod package `herdr-containers` plus its requirements:
the `hc-devcontainer` host helper and an optional Herdr Core dev container
Feature. It adds Containers to Herdr's main sidebar.

Read the code. This file holds the rules, traps, and decisions that reading
alone will not explain. Read `herdr-containers/RECIPE.md` before changing the
package. If `AGENTS.local.md` exists, read it too.

## The rule

Add-ons only. Never modify Herdr or `herdr-core` for this package. The four
`*-containers` mods are generated clones: change their anchors in
`tools/patch-mods.py`, then run `tools/build-package.py`. Never hand-edit
`herdr-containers/mods/*-containers`. `herdr-containers/mods/containers` is the
only hand-written mod; its tests are in `test/test_containers.cjs`.

## Rules that aren't in the code

- Lean, KISS. Use stdlib Python, POSIX sh, and plain JavaScript. No new
  dependency or abstraction without a real need.
- TDD for behavior changes. Test the required behavior first; keep tests
  focused and fast. Report what was observed, never what was assumed.
- Terse comments; explain surprises, not the obvious.
- No hover background on Containers section or group headers. Rows and their
  × control hover like Spaces rows.
- Counts belong only on subgroup headers, in Agents group-header style.
  Folding collapses rows; it does not hide or discard them.
- Disconnect updates the row and Spaces group optimistically and instantly.
  Plain containers need an inline Connect confirmation.
- Container visibility settings live in footer Settings → Containers, not
  on the Containers pane.

## Traps

- The app runs `hc-devcontainer` (directly and through `hc machine add
  --install`) and core runs it for every connection, so both PATHs need it. If
  core's PATH lacks it, the shell exits 127 and `hc machine add` can say “hc is
  not installed.” Restart core after a PATH change.
- With the `devcontainer` CLI on core's PATH, every connection replays VS
  Code's open (read from the extension's bundle; see README "VS Code
  parity"): `up --skip-post-create`, then `run-user-commands` phases split by
  `--skip-non-blocking-commands` / `--stop-for-personalization`, with VS Code's
  personalization between, the rest in a forked child, then `devcontainer
  exec`. Without the CLI, or when it exits 127 (no `node`), the helper uses
  `docker exec`. `up` always gets `--expect-existing-container` and exact id
  labels: it must never build. stdout is the connection and stdin is core's:
  CLI output goes to the log, and the forked child must hold neither pipe.
  The engine is VS Code's own when its `cli-bin` record exists (Electron with
  `ELECTRON_RUN_AS_NODE=1`); VS Code's `devcontainer` shim has only
  open/build and must never be run. Tests pin `HC_DEVCONTAINER_CLI=""`.
- Shipped Spaces caches machines for 15 s; the app polls `machine.list`
  about every 2.5 s. `space.close` only queues closure.
- Tests and smokes use isolated `HC_SESSION` and scratch `HERDR_APP_HOME`.
  Never touch the real core, app, or agents. Clean up scratch state.
- `herdr-app shot` returns the last drawn frame; it can be stale when the
  window is unfocused. `herdr-app check` needs a display.
- `sh -n file1 file2` checks only the first file. Check each script alone.
- A dev container needs a folder label, or both a `vsc-*` image and
  devcontainer metadata. Metadata on another image alone is not enough.

## Testing

Run `sh tools/check.sh` before proposing a commit. It runs Python and Node
tests, syntax and JSON checks, the mod package drift test, and a whitespace check
(`git diff --check` over tracked and untracked files). The drift test needs
`../herdr-app/defaults`; CI fails if it is absent.
CI runs on owner-started pushes to main or manual dispatch. The daily rebuild
workflow is the exception: it only opens review PRs or failure issues.

- After changing `requirements/feature/herdr-core`, run `test/sync-feature.sh`;
  `test/test_feature.py` fails until the `test/.devcontainer` copy matches.
- To build from an installed app home (no `Cargo.toml`), pass its Herdr
  version: `python3 -I tools/build-package.py --source <app-home>
  --source-version X.Y.Z`.
- The targeted Herdr version comes from the four clones' `mod.json`
  `cloned_from.version`; README, RECIPE, and CI must name the same one
  (`test/test_build_package.py` checks).

## Updating for a new Herdr release

Read the new `herdr-app` defaults. Rebuild with
`python3 -I tools/build-package.py --source <herdr-app>/defaults`. If an
anchor fails, fix `tools/patch-mods.py`, then rebuild. Run
`python3 -I tools/retarget-herdr.py --version X.Y.Z --sha <tag commit SHA>`
to update docs, CI, and the changelog. Run `sh tools/check.sh`,
compare the drift test, and live-verify all five mods and the sidebar in a
scratch app home before release. Swapped clones are frozen copies: they miss
later fixes to shipped mods until rebuilt and reinstalled.

## Releasing and publishing

Publish as a public mod repo with topic `herdr-mod` (`gh repo edit
--remove-topic herdr-layout --add-topic herdr-mod`). Keep
`herdr-containers/RECIPE.md` current and a real `herdr-containers/preview.png`.
Ship the five mods and global `keys.json`, with no `layouts/` folder. Update
the changelog and versions with each release. Publishing and changes to the
person's real app home require their explicit OK.

## Contributing

Work on a branch and open a pull request. Use lowercase conventional commit
messages.
