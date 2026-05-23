#!/usr/bin/env bash
# release.sh — one-command release for fluid-postgres-mcp.
#
# Executes the README §Release flow as a single transaction:
# CHANGELOG + version bump → build → inspect → push → draft GH
# Release → PyPI upload → flip Release → smoke → release-check.
#
# Two modes:
#   non-interactive (primary, AI-driven): all inputs via flags.
#   interactive   (fallback, human-driven): missing inputs prompt
#                  for $EDITOR; the rules are appended to the empty
#                  buffer so the author sees what to write.
#
# Usage:
#   scripts/release.sh --version X.Y.Z \
#       --changelog-file PATH \
#       --release-body-file PATH \
#       [--commit-message STR] [--tag-message STR] [--yes]
#
#   scripts/release.sh                    # fully interactive
#   scripts/release.sh --version 0.1.4    # interactive for the rest
#
# Exit codes:
#   0  released; release-check.sh agrees on tag/GH Release/PyPI
#   1  caller error (bad args, dirty tree, version mismatch …)
#   2  step 2-5 failure (pre-PyPI; safe to retry after fix)
#   3  PyPI upload failure BEFORE pypi accepted anything (retry safe)
#   4  PyPI upload failure AFTER acceptance — catastrophe class;
#      orphan GH draft must be flipped manually
#   5  step 7 failure (Release flipped or smoke failed); PyPI is
#      fine, fix the surface release-check.sh reports red on
set -euo pipefail

PKG="fluid-postgres-mcp"
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || { echo "not a git repo" >&2; exit 1; })
cd "$REPO_ROOT"

# ──────────────────────────────────────────────────────────────────
# args
# ──────────────────────────────────────────────────────────────────
VERSION=""
CHANGELOG_FILE=""
RELEASE_BODY_FILE=""
COMMIT_MSG=""
TAG_MSG=""
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)            VERSION="$2"; shift 2 ;;
        --changelog-file)     CHANGELOG_FILE="$2"; shift 2 ;;
        --release-body-file)  RELEASE_BODY_FILE="$2"; shift 2 ;;
        --commit-message)     COMMIT_MSG="$2"; shift 2 ;;
        --tag-message)        TAG_MSG="$2"; shift 2 ;;
        --yes|-y)             ASSUME_YES=1; shift ;;
        -h|--help)
            sed -n '/^# /,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

# ──────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────
die() { echo "release.sh: $*" >&2; exit 1; }
log() { echo "==> $*"; }

confirm() {
    local prompt="$1"
    [[ $ASSUME_YES -eq 1 ]] && return 0
    [[ ! -t 0 ]] && die "stdin is not a tty; pass --yes for non-interactive"
    read -r -p "$prompt [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

editor_capture() {
    # Open $EDITOR on a temp file pre-populated with rule text as
    # comments, return the user's content stripped of '# ' lines.
    local label="$1" hint="$2" target
    [[ ! -t 0 ]] && die "missing --$label-file and stdin is not a tty"
    target=$(mktemp -t "release-${VERSION}-${label}.XXXXXX")
    {
        echo "# $label — fluid-postgres-mcp $VERSION"
        echo "# Lines starting with '# ' are stripped on save."
        echo "#"
        echo "$hint" | sed 's/^/# /'
        echo
    } > "$target"
    "${EDITOR:-vi}" "$target"
    grep -v '^# ' "$target" | sed '/./,$!d'   # strip leading blank lines after comments
}

require_tag_missing() {
    git rev-parse -q --verify "refs/tags/$1" >/dev/null && \
        die "tag $1 already exists locally — bump version or delete tag"
    return 0
}

cleanup_draft() {
    # Idempotent: deletes the draft GH Release for this version if
    # it exists AND is still in draft. Never touches a published one.
    local tag="v$VERSION" state
    state=$(gh release view "$tag" --json isDraft -q .isDraft 2>/dev/null || echo "")
    if [[ "$state" == "true" ]]; then
        log "cleanup: deleting draft GH Release $tag"
        gh release delete "$tag" -y >/dev/null 2>&1 || true
    fi
}

# ──────────────────────────────────────────────────────────────────
# step 0: validation
# ──────────────────────────────────────────────────────────────────
log "step 0: validate inputs and working tree"

for cmd in git gh uvx tar curl jq awk sed; do
    command -v "$cmd" >/dev/null 2>&1 || die "missing: $cmd"
done

if [[ -z "$VERSION" ]]; then
    [[ ! -t 0 ]] && die "missing --version"
    read -r -p "Version (X.Y.Z): " VERSION
fi
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-z][a-z0-9.+-]*)?$ ]] || \
    die "version '$VERSION' is not SemVer-shaped"
TAG="v$VERSION"

git diff --quiet || die "working tree is dirty; commit or stash first"
git diff --staged --quiet || die "staged changes exist; commit or unstage first"
require_tag_missing "$TAG"

# Confirm pyproject already declares the new version — refuse to
# proceed if not. The author owns the bump; the script verifies.
declared=$(awk -F'"' '/^version = "/{print $2; exit}' pyproject.toml)
[[ "$declared" == "$VERSION" ]] || \
    die "pyproject.toml declares version $declared, expected $VERSION — bump it first"

# Confirm CHANGELOG already has a [$VERSION] section.
grep -q "^## \[$VERSION\]" CHANGELOG.md || \
    die "CHANGELOG.md has no [$VERSION] section — add it first"

# ──────────────────────────────────────────────────────────────────
# step 0b: gather author-work inputs
# ──────────────────────────────────────────────────────────────────
CHANGELOG_BODY=$(awk -v v="$VERSION" '
    $0 ~ "^## \\[" v "\\]" {flag=1; next}
    flag && $0 ~ "^## \\[" {flag=0}
    flag {print}
' CHANGELOG.md)

if [[ -z "$RELEASE_BODY_FILE" ]]; then
    log "step 0b: capturing Release body via \$EDITOR"
    hint="Audience: someone glancing at the release page, deciding whether
this needs their attention now. Open with the most consequential
change. If the release has a coherent theme, name it; otherwise
don't invent one. Follow with a Highlights bullet list of what a
reader needs to know without opening CHANGELOG. Breaking, security,
deprecations, platform/Python/dependency shifts, major features
will usually qualify; pure bug fixes and internal changes will not.
End with a CHANGELOG link. Hand-written; do not paste the CHANGELOG.

For reference, the [$VERSION] CHANGELOG entry says:

$CHANGELOG_BODY"
    RELEASE_BODY_CONTENT=$(editor_capture "release-body" "$hint")
    [[ -n "$(echo "$RELEASE_BODY_CONTENT" | tr -d '[:space:]')" ]] || \
        die "release body is empty"
    RELEASE_BODY_FILE=$(mktemp -t "release-${VERSION}-body.XXXXXX.md")
    printf '%s\n' "$RELEASE_BODY_CONTENT" > "$RELEASE_BODY_FILE"
else
    [[ -f "$RELEASE_BODY_FILE" ]] || die "release body file not found: $RELEASE_BODY_FILE"
fi

COMMIT_MSG="${COMMIT_MSG:-chore(release): bump version to $VERSION}"
TAG_MSG="${TAG_MSG:-Release $VERSION}"

# ──────────────────────────────────────────────────────────────────
# step 1: commit + tag (CHANGELOG and pyproject are already staged
# by the author and committed OR are the working-tree change we're
# about to commit)
# ──────────────────────────────────────────────────────────────────
log "step 1: commit CHANGELOG + pyproject and tag $TAG"

# If neither is modified vs HEAD, assume the release commit already
# exists; just verify HEAD touches them.
if git diff --quiet HEAD -- CHANGELOG.md pyproject.toml; then
    head_msg=$(git log -1 --format="%s")
    log "  CHANGELOG and pyproject are clean at HEAD ('$head_msg'); assuming release commit already exists"
else
    git add CHANGELOG.md pyproject.toml
    git commit -m "$COMMIT_MSG"
fi
git tag -a "$TAG" -m "$TAG_MSG"

# From here on, step 2-5 failures must clean up the local tag and
# any draft GH Release. Step 6+ is the point of no return.
PRE_PYPI=1
trap 'rc=$?; if [[ $PRE_PYPI -eq 1 ]]; then
        echo "release.sh: pre-PyPI failure; cleaning up local tag and draft" >&2
        git tag -d "'"$TAG"'" >/dev/null 2>&1 || true
        cleanup_draft
      fi; exit $rc' ERR

# ──────────────────────────────────────────────────────────────────
# step 2: clean and build
# ──────────────────────────────────────────────────────────────────
log "step 2: clean + build"
rm -rf dist/ build/ ./*.egg-info
uvx --from build pyproject-build >/dev/null

# ──────────────────────────────────────────────────────────────────
# step 3: inspect sdist + twine check
# ──────────────────────────────────────────────────────────────────
log "step 3: inspect sdist + twine check"
listing=$(tar -tzf dist/*.tar.gz | sort)
echo "$listing"
for forbidden in '\.env' '\.claude' '^tasks/'; do
    if echo "$listing" | grep -E "$forbidden" >/dev/null; then
        die "sdist leak detected: $forbidden — fix the allowlist"
    fi
done
.venv/bin/twine check dist/*

# ──────────────────────────────────────────────────────────────────
# step 4: push commit and tag
# ──────────────────────────────────────────────────────────────────
log "step 4: push commit and tag"
git push
git push origin "$TAG"

# ──────────────────────────────────────────────────────────────────
# step 5: draft GH Release
# ──────────────────────────────────────────────────────────────────
log "step 5: draft GitHub Release"
gh release create "$TAG" --draft -t "$TAG" -F "$RELEASE_BODY_FILE" >/dev/null
log "  draft created — review at: $(gh release view "$TAG" --json url -q .url)"

confirm "Draft looks good, proceed to PyPI upload (POINT OF NO RETURN)?" || \
    die "aborted before PyPI; draft retained, tag pushed"

# ──────────────────────────────────────────────────────────────────
# step 6: PyPI upload — point of no return
# ──────────────────────────────────────────────────────────────────
log "step 6: upload to PyPI (POINT OF NO RETURN)"
PRE_PYPI=0   # disable the pre-PyPI cleanup trap
trap - ERR

[[ -f .env ]] || die "step 6: .env not found"
# shellcheck disable=SC1091
set -a; source .env; set +a
[[ -n "${PYPI_TOKEN:-}" ]] || die "step 6: PYPI_TOKEN not set after sourcing .env"

upload_out=$(mktemp)
if ! .venv/bin/twine upload -u __token__ -p "$PYPI_TOKEN" dist/* 2> >(sed 's/pypi-[A-Za-z0-9_-]*/pypi-<REDACTED>/g' >&2) > "$upload_out"; then
    # Distinguish "PyPI rejected upload" (safe to retry) from "PyPI
    # accepted then errored mid-flight" (catastrophe). twine prints
    # "View at:" only on success of at least one file.
    if grep -q "View at:" "$upload_out"; then
        echo "release.sh: PyPI partially accepted; flip GH draft manually with:" >&2
        echo "  gh release edit $TAG --draft=false" >&2
        rm -f "$upload_out"
        exit 4
    fi
    rm -f "$upload_out"
    echo "release.sh: PyPI upload failed before acceptance; safe to retry" >&2
    exit 3
fi
sed 's/pypi-[A-Za-z0-9_-]*/pypi-<REDACTED>/g' "$upload_out"
rm -f "$upload_out"

# ──────────────────────────────────────────────────────────────────
# step 7: flip GH Release and smoke
# ──────────────────────────────────────────────────────────────────
log "step 7: flip GH Release to published"
gh release edit "$TAG" --draft=false >/dev/null

log "step 7: smoke uvx fluid-postgres-mcp --version"
# PyPI's CDN can lag a few seconds after upload.
for attempt in 1 2 3 4 5; do
    if smoke_ver=$(uvx --refresh "$PKG" --version 2>/dev/null) && \
       [[ "$smoke_ver" == "$PKG $VERSION" ]]; then
        break
    fi
    if [[ $attempt -eq 5 ]]; then
        echo "release.sh: smoke failed after 5 attempts (got: ${smoke_ver:-<none>}); release-check.sh will report red until PyPI CDN catches up" >&2
        exit 5
    fi
    sleep $((attempt * 2))
done
log "  smoke OK: $smoke_ver"

# ──────────────────────────────────────────────────────────────────
# step 8: release-check
# ──────────────────────────────────────────────────────────────────
log "step 8: release-check.sh agreement"
"$REPO_ROOT/scripts/release-check.sh" "$TAG"

log "DONE: $TAG fully released"
