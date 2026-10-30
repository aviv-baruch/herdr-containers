#!/bin/sh
set -eu

fail() { printf 'herdr-core feature: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail 'install.sh must run as root'
remote_user=${_REMOTE_USER:-}
remote_home=${_REMOTE_USER_HOME:-}
[ -n "$remote_user" ] && [ -n "$remote_home" ] || fail 'missing _REMOTE_USER or _REMOTE_USER_HOME'
uid=$(id -u "$remote_user") || fail "unknown remote user: $remote_user"
gid=$(id -g "$remote_user") || fail "unknown remote user: $remote_user"

set --
command -v curl >/dev/null 2>&1 || set -- "$@" curl
command -v tar >/dev/null 2>&1 || set -- "$@" tar
if [ "$#" -gt 0 ]; then
    if command -v apt-get >/dev/null 2>&1; then
        { apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"; } \
            || fail "cannot install $* with apt"
        rm -rf /var/lib/apt/lists/*
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache "$@" || fail "cannot install $* with apk"
    else
        fail "needs $* (no apt-get or apk found)"
    fi
fi

case "$(uname -s)" in
    Linux) os=linux ;;
    *) fail 'only Linux containers are supported' ;;
esac
case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    arm64 | aarch64) arch=aarch64 ;;
    *) fail "no build for $(uname -m)" ;;
esac
target="$os-$arch"
releases=${HERDR_RELEASES_URL:-https://releases.herdr.dev}
version=${VERSION:-latest}
case $version in
    latest) manifest="$releases/core/latest.json" ;;
    *[!a-zA-Z0-9._-]* | '' | [._-]* | *..*) fail "invalid version: $version" ;;
    *) manifest="$releases/core/$version/manifest.json" ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' 0
trap 'exit 1' 1 2 3 15
curl -fsSL "$manifest" -o "$tmp/manifest.json" || fail "cannot reach $manifest"
flat=$(tr -d '\n ' < "$tmp/manifest.json")
entry=$(printf '%s' "$flat" | sed -n "s/.*\"$target\":{\([^}]*\)}.*/\1/p")
url=$(printf '%s' "$entry" | sed -n 's/.*"url":"\([^"]*\)".*/\1/p')
want=$(printf '%s' "$entry" | sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p')
[ -n "$url" ] && [ -n "$want" ] || fail "no build for $target in $manifest"
case $want in
    *[!a-fA-F0-9]* | '') fail 'invalid sha256 in manifest' ;;
esac
[ "${#want}" -eq 64 ] || fail 'invalid sha256 in manifest'

curl -fsSL "$url" -o "$tmp/hc.tar.gz" || fail "download failed: $url"
if command -v sha256sum >/dev/null 2>&1; then
    got=$(sha256sum "$tmp/hc.tar.gz")
elif command -v shasum >/dev/null 2>&1; then
    got=$(shasum -a 256 "$tmp/hc.tar.gz")
else
    fail 'needs sha256sum or shasum to verify the download'
fi
[ "${got%% *}" = "$want" ] || fail 'checksum mismatch'
tar -xzf "$tmp/hc.tar.gz" -C "$tmp" || fail 'cannot unpack release archive'
[ -f "$tmp/herdr-core" ] || fail 'release archive has no herdr-core'
chmod 755 "$tmp/herdr-core"
"$tmp/herdr-core" --version >/dev/null 2>&1 || fail 'downloaded herdr-core cannot run'

# hc updates a machine by swapping ~/.local/bin/herdr-core (machines.rs install); docker
# exec's PATH has no ~/.local/bin, so /usr/local/bin links there instead of shadowing it.
bin="$remote_home/.local/bin"
mkdir -p "$bin" "$remote_home/.local/state"
cp "$tmp/herdr-core" "$bin/herdr-core"
chmod 755 "$bin/herdr-core"
chown "$uid:$gid" "$remote_home/.local" "$bin" "$bin/herdr-core" "$remote_home/.local/state"
mkdir -p /usr/local/bin
ln -sf "$bin/herdr-core" /usr/local/bin/herdr-core
if ! command -v hc >/dev/null 2>&1 && [ ! -e /usr/local/bin/hc ] && [ ! -L /usr/local/bin/hc ]; then
    ln -s herdr-core /usr/local/bin/hc
fi

# A new core id when /etc/machine-id changes (herdr-core server.rs load_identity).
case $(cat /etc/machine-id 2>/dev/null || true) in
    '' | uninitialized) ;;
    *) printf 'herdr-core feature: warning: /etc/machine-id is set; unless it stays the same across rebuilds, the core id changes\n' >&2 ;;
esac

# Open and sticky like /tmp: the devcontainers CLI may remap the user's UID after this
# build (chowning only $HOME), and hc makes its session folder in here 0700 itself.
state=/var/lib/herdr-core-state
mkdir -p "$state"
chmod 1777 "$state"
link="$remote_home/.local/state/herdr-core"
if [ -L "$link" ]; then
    [ "$(readlink "$link")" = "$state" ] || fail "$link points elsewhere"
elif [ -d "$link" ]; then
    rmdir "$link" 2>/dev/null || fail "$link has state; move it before enabling this feature"
    ln -s "$state" "$link"
elif [ -e "$link" ]; then
    fail "$link exists; move it before enabling this feature"
else
    ln -s "$state" "$link"
fi
