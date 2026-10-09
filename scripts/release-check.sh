#!/usr/bin/env bash
# release-check.sh — assert git tag, GitHub Release, and PyPI all agree
# for a given version (or the latest git tag if none specified).
#
# Usage: scripts/release-check.sh           # checks latest tag
#        scripts/release-check.sh v0.1.3    # checks specific tag
#        scripts/release-check.sh 0.1.3     # leading 'v' optional
#
# Exit codes:
#   0  all three sources present and match
#   1  at least one source missing or disagreeing
#   2  prerequisite missing (gh, curl) or not in a git repo
set -euo pipefail

PKG="fluid-postgres-mcp"

for cmd in git gh curl; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "missing: $cmd" >&2; exit 2; }
done

git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo" >&2; exit 2; }

if [[ $# -eq 0 ]]; then
    tag=$(git tag -l 'v[0-9]*' --sort=-v:refname | head -1)
    [[ -n "$tag" ]] || { echo "no version tags found" >&2; exit 2; }
else
    tag="${1#v}"
    tag="v$tag"
fi
ver="${tag#v}"

# with_timeout SECS CMD... — run CMD, TERM it after SECS seconds
# (macOS ships no `timeout`).
with_timeout() {
    local secs=$1; shift
    "$@" &
    local pid=$!
    (
        for ((i = 0; i < secs; i++)); do
            sleep 1
            kill -0 "$pid" 2>/dev/null || exit 0
        done
        echo "release-check.sh: timed out after ${secs}s: $*" >&2
        kill -TERM "$pid" 2>/dev/null
    ) &
    local watcher=$!
    local rc=0
    wait "$pid" || rc=$?
    wait "$watcher" 2>/dev/null || true
    return "$rc"
}

echo "Checking release agreement for $tag …"

origin_tag=$(with_timeout 30 git ls-remote --tags origin "refs/tags/$tag" \
    | awk '{print $2}' | sed 's#^refs/tags/##' || true)
printf "  git tag origin: %s\n" "${origin_tag:-MISSING}"
local_tag=$(git tag -l "$tag")
printf "  git tag local : %s\n" "${local_tag:-missing (informational)}"
gh_state=$(with_timeout 30 gh release view "$tag" --json tagName,isDraft \
    -q 'if .isDraft then .tagName + " (draft)" else .tagName end' 2>/dev/null || true)
printf "  GitHub Release: %s\n" "${gh_state:-MISSING}"
pypi_ver=$(curl -sfL --max-time 30 "https://pypi.org/pypi/${PKG}/${ver}/json" \
    | jq -r .info.version 2>/dev/null || true)
printf "  PyPI version  : %s\n" "${pypi_ver:-MISSING}"

ok=0

[[ "$origin_tag" == "$tag" ]] || ok=1
[[ "$gh_state"   == "$tag" ]] || ok=1
[[ "$pypi_ver"   == "$ver" ]] || ok=1

if [[ $ok -eq 0 ]]; then
    echo "OK: $tag is fully released."
    exit 0
else
    echo "FAIL: $tag is half-released — fix the missing source(s) above." >&2
    exit 1
fi
