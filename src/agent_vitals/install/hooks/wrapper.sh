#!/usr/bin/env bash
# av-hooks wrapper — installed by `av hooks install`. Do not edit.
#
# Gates the binary whose name matches $0 (with any trailing .disabled stripped)
# on a fresh agent-vitals stamp via `av hooks gate`. Reads are never gated.
# When the wrapper is disabled (renamed *.disabled), this script exits
# immediately so the gating is off but the file remains on disk.

set -e

# A .disabled suffix means hooks are temporarily off — short-circuit.
case "$0" in
    *.disabled)
        # Find the real binary bypassing our wrapper dir, then exec un-gated.
        SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
        CLEANED_PATH="$(echo "$PATH" | tr ':' '\n' | grep -v "^${SELF_DIR}$" | paste -sd :)"
        REAL="$(PATH="$CLEANED_PATH" command -v "$(basename "$0" .disabled)" 2>/dev/null || true)"
        if [[ -z "$REAL" ]]; then
            # System fallback
            for d in /usr/bin /bin /usr/local/bin /usr/sbin /sbin; do
                if [[ -x "$d/$(basename "$0" .disabled)" ]]; then
                    REAL="$d/$(basename "$0" .disabled)"
                    break
                fi
            done
        fi
        if [[ -z "$REAL" ]]; then
            echo "av-hooks(disabled): cannot locate real $(basename "$0" .disabled)" >&2
            exit 127
        fi
        exec "$REAL" "$@"
        ;;
esac

BIN="$(basename "$0")"

# av binary lives one dir up from the hooks dir, by uv-tool-install convention.
HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
AV_BIN="$(dirname "$HOOKS_DIR")/av"

if [[ ! -x "$AV_BIN" ]]; then
    echo "av-hooks: cannot locate 'av' binary at $AV_BIN — is agent-vitals installed?" >&2
    exit 127
fi

exec "$AV_BIN" hooks gate "$BIN" "$@"
