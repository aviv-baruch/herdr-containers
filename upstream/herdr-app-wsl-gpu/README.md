# Herdr on WSL2: draw on the GPU

A patch for [herdrdev/herdr-app](https://github.com/herdrdev/herdr-app)
v0.1.17, kept here so it can be tested before it goes upstream. It is not part
of the herdr-containers package.

## The bug

In WSL2, the Herdr window draws every frame on the CPU. About 16 to 32
`llvmpipe` threads use 200 to 500% CPU.

WSL2 reaches the GPU only through `/dev/dxg`, which speaks Direct3D 12.

- Vulkan: Ubuntu's Mesa has no Vulkan-to-D3D12 driver (dzn), so the only
  Vulkan device is lavapipe, which runs on the CPU.
- OpenGL: Mesa tries zink first, and zink refuses lavapipe. EGL fails with
  "ZINK: failed to choose pdev" and "egl: failed to create dri2 screen".

Mesa's d3d12 OpenGL driver is installed (`d3d12_dri.so`), but Mesa only uses
it when `GALLIUM_DRIVER=d3d12` names it.

## The fix

The new file is `src/wsl_gpu.rs`. At startup, before any thread exists,
Herdr sets `GALLIUM_DRIVER=d3d12` only when all of these hold:

- it is Linux and WSL: the kernel release names Microsoft or WSL, or
  `WSL_DISTRO_NAME` is set;
- `/dev/dxg` exists;
- you set none of `GALLIUM_DRIVER` (even empty), `LIBGL_ALWAYS_SOFTWARE` or
  `MESA_LOADER_DRIVER_OVERRIDE`.

When it does, the app log says
`WSL: drawing on the GPU through Direct3D 12`.

Programs Herdr starts never get the variable. That covers the core and so
every terminal, mods' programs, git, curl, the link opener and the update
relaunch. Other platforms, and Linux outside WSL, are unchanged.

If d3d12 can't start, OpenGL fails and Herdr draws with lavapipe, as it does
today.

## Build it

```sh
git clone --recurse-submodules --branch v0.1.17 https://github.com/herdrdev/herdr-app
cd herdr-app
git am /path/to/upstream/herdr-app-wsl-gpu/0001-*.patch
cargo build --locked
```

Use Rust 1.95.0 and Zig 0.16.0. `pip install ziglang==0.16.0` works; set
`ZIG` to its `zig` binary if it is not on PATH.

Ubuntu also needs these packages:

```sh
sudo apt install pkg-config clang libclang-dev libxcb1-dev libxkbcommon-dev \
  libxkbcommon-x11-dev libwayland-dev libvulkan-dev mesa-vulkan-drivers \
  libfontconfig-dev libfreetype-dev mesa-utils
```

## Check WSL first

```sh
ls -l /dev/dxg                                   # must exist
GALLIUM_DRIVER=d3d12 glxinfo -B | grep renderer  # names your GPU: "D3D12 (...)"
```

If `glxinfo` names `llvmpipe` or fails, d3d12 can't reach your GPU, and the
fix changes nothing for you.

## Run it next to your real Herdr

Use a scratch home and its own core session. Never run the build without
them: it would open a second window on your real setup.

```sh
export HERDR_APP_HOME=$(mktemp -d) HC_SESSION=herdr-wsl-test
export XDG_STATE_HOME=$HERDR_APP_HOME/state
HERDR_APP_LOG=warn,herdr_app=info,gpui_wgpu=info \
  target/debug/herdr-app >"$HERDR_APP_HOME/app.log" 2>&1 & app=$!
```

`hc` (herdr-core) must be on PATH.

## Verify

1. The log names the GPU:

   ```sh
   grep -E 'WSL:|Selected GPU adapter' "$HERDR_APP_HOME/app.log"
   ```

   Expect the `WSL:` line, then `Selected GPU adapter: "D3D12 (<your GPU>)" (Gl)`.

2. No CPU drawing threads. This prints `0`; before the fix it was about 16 to 32:

   ```sh
   ps -L -o comm= -p $app | grep -c llvmpipe
   ```

3. Terminals don't inherit the variable. In a terminal in the test window,
   run the command below. It prints `unset`, or whatever you set yourself.

   ```sh
   echo "${GALLIUM_DRIVER-unset}"
   ```

4. The old path is still available. Restart the same binary with
   `GALLIUM_DRIVER=` (empty). There is no `WSL:` line, lavapipe is selected,
   and the `llvmpipe` threads are back.

For a performance comparison, run this same binary twice with the same window
size and load: once with `GALLIUM_DRIVER=` (the old CPU path), once without
it (the fix). Compare the app's CPU time over the same period:

```sh
ps -o times= -p $app
```

`cargo build` is a dev build: dependencies are optimized, Herdr itself is not.
Use `cargo build --release --locked` for absolute numbers.

Clean up afterwards:

```sh
kill $app
hc server stop
rm -rf "$HERDR_APP_HOME"
```

`hc server stop` stops only the test core, as long as `HC_SESSION` is still
set in that shell.

## Tested so far

Tested on Linux only: Ubuntu 24.04, Mesa 25.2.8, no GPU.

- The full `cargo test`, `bun test` and docs checks pass. The suites match
  unmodified v0.1.17, plus 5 new tests.
- In a sandbox with a simulated WSL2 (a placeholder `/dev/dxg`):
  - the app named d3d12;
  - Mesa honored it, so OpenGL failed with no real D3D12 behind it;
  - the window still drew with lavapipe;
  - the core, a terminal shell and `xdg-open` had no `GALLIUM_DRIVER`.

Not tested yet: real WSL2 with a real GPU (including sleep and resume), and
Windows and macOS builds. On those platforms the change does nothing.
