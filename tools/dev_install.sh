#!/usr/bin/env bash
# Link this checkout into Blender as an extension, so edits are live.
#
# The macOS and Linux counterpart to dev_install.ps1. A symlink from Blender's
# user_default extensions folder to blender/BB_pipeline in this repository:
# nothing is copied, so editing the add-on or the shared core and restarting
# Blender is the whole iteration loop.
#
# The link is created in Blender's own support folder, which is on the boot
# disk. That matters when the checkout lives on an external drive - an exFAT
# volume cannot hold a symlink at all, but it can perfectly well be the target
# of one.
#
#   tools/dev_install.sh              link into every Blender found
#   tools/dev_install.sh 4.5          link into one version
#   tools/dev_install.sh --remove     unlink instead
#
# Enable it once in Blender under Edit > Preferences > Add-ons; the setting
# sticks across restarts.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON="$REPO/blender/BB_pipeline"

case "$(uname -s)" in
    Darwin) SUPPORT="$HOME/Library/Application Support/Blender" ;;
    *)      SUPPORT="${XDG_CONFIG_HOME:-$HOME/.config}/blender" ;;
esac

remove=0
wanted=""
for argument in "$@"; do
    case "$argument" in
        --remove|-r) remove=1 ;;
        *) wanted="$argument" ;;
    esac
done

if [ ! -d "$ADDON" ]; then
    echo "no add-on at $ADDON" >&2
    exit 1
fi

if [ ! -d "$SUPPORT" ]; then
    echo "no Blender support folder at $SUPPORT" >&2
    exit 1
fi

found=0
for versioned in "$SUPPORT"/*/; do
    version="$(basename "$versioned")"
    [ -n "$wanted" ] && [ "$version" != "$wanted" ] && continue

    # Only versions that actually have the extensions layout; 4.2 introduced it.
    parent="$versioned/extensions/user_default"
    case "$version" in
        [0-9]*) ;;
        *) continue ;;
    esac

    mkdir -p "$parent"
    link="$parent/BB_pipeline"

    if [ "$remove" = "1" ]; then
        if [ -L "$link" ]; then
            rm "$link"
            echo "$version  unlinked"
            found=1
        elif [ -e "$link" ]; then
            echo "$version  left alone - a real folder, not a link" >&2
        fi
        continue
    fi

    if [ -e "$link" ] && [ ! -L "$link" ]; then
        # A real folder here is an installed copy that would win over the
        # checkout and silently run old code. Say so rather than clobber it.
        echo "$version  skipped - $link is a real folder; remove it first" >&2
        continue
    fi

    ln -sfn "$ADDON" "$link"
    echo "$version  -> $ADDON"
    found=1
done

if [ "$found" = "0" ]; then
    echo "nothing to do" >&2
    exit 1
fi
