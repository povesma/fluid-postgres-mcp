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

echo "Checking release agreement for $tag …"

git_tag=$(git tag -l "$tag")
gh_tag=$(gh release view "$tag" --json tagName -q .tagName 2>/dev/null || true)
pypi_ver=$(curl -sfL "https://pypi.org/pypi/${PKG}/${ver}/json" \
    | jq -r .info.version 2>/dev/null || true)

ok=0
printf "  git tag       : %s\n" "${git_tag:-MISSING}"
printf "  GitHub Release: %s\n" "${gh_tag:-MISSING}"
printf "  PyPI version  : %s\n" "${pypi_ver:-MISSING}"

[[ "$git_tag" == "$tag" ]] || ok=1
[[ "$gh_tag"  == "$tag" ]] || ok=1
[[ "$pypi_ver" == "$ver" ]] || ok=1

if [[ $ok -eq 0 ]]; then
    echo "OK: $tag is fully released."
    exit 0
else
    echo "FAIL: $tag is half-released — fix the missing source(s) above." >&2
    exit 1
fi
