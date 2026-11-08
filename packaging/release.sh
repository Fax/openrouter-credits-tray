#!/usr/bin/env bash
# Cut a new release: bump __version__, commit, tag, and (after confirmation)
# push. Pushing the tag makes GitHub Actions build the .deb and publish the
# GitHub Release.
#
#   ./packaging/release.sh patch        1.0.0 -> 1.0.1
#   ./packaging/release.sh minor        1.0.0 -> 1.1.0
#   ./packaging/release.sh major        1.0.0 -> 2.0.0
#   ./packaging/release.sh 1.2.3        explicit version
#
# Options:
#   --dry-run    show what would happen, change nothing
#   --no-push    commit and tag locally, do not push
#   --yes        push without asking
#
# Checks before changing anything: on the release branch, clean working tree,
# in sync with origin, tag does not exist yet, new version is higher, tests pass
# and the .deb builds.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
APP="openrouter-credits-tray.py"
BRANCH="${RELEASE_BRANCH:-master}"
REMOTE="${RELEASE_REMOTE:-origin}"

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
die() { echo "release: $*" >&2; exit 1; }

BUMP=""
DRY_RUN=0
PUSH=ask
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-push) PUSH=no ;;
        --yes|-y) PUSH=yes ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option: $arg" ;;
        *) [ -z "$BUMP" ] || die "only one version argument allowed"; BUMP="$arg" ;;
    esac
done
[ -n "$BUMP" ] || { usage; exit 2; }

cd "$ROOT"

CURRENT="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$APP" | head -1)"
[[ "$CURRENT" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] \
    || die "current __version__ '${CURRENT}' is not X.Y.Z"
MAJOR="${BASH_REMATCH[1]}"; MINOR="${BASH_REMATCH[2]}"; PATCH="${BASH_REMATCH[3]}"

case "$BUMP" in
    major) NEW="$((MAJOR + 1)).0.0" ;;
    minor) NEW="${MAJOR}.$((MINOR + 1)).0" ;;
    patch) NEW="${MAJOR}.${MINOR}.$((PATCH + 1))" ;;
    *)
        NEW="${BUMP#v}"
        [[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
            || die "'${BUMP}' is not major, minor, patch or X.Y.Z"
        ;;
esac
TAG="v${NEW}"

# the new version must sort strictly after the current one
if [ "$NEW" = "$CURRENT" ] \
    || [ "$(printf '%s\n%s\n' "$CURRENT" "$NEW" | sort -V | tail -1)" != "$NEW" ]; then
    die "new version ${NEW} is not higher than ${CURRENT}"
fi

echo "==> ${CURRENT} -> ${NEW} (tag ${TAG})"

# ---------------------------------------------------------------- checks

[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] \
    || die "not on ${BRANCH} (on $(git rev-parse --abbrev-ref HEAD))"
[ -z "$(git status --porcelain)" ] \
    || die "working tree is not clean; commit or stash first"

echo "==> fetching ${REMOTE}"
git fetch --quiet --tags "$REMOTE"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse "${REMOTE}/${BRANCH}")"
[ "$LOCAL_HEAD" = "$REMOTE_HEAD" ] \
    || die "${BRANCH} is not in sync with ${REMOTE}/${BRANCH}; pull or push first"

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    die "tag ${TAG} already exists"
fi

echo "==> running tests"
make --no-print-directory test >/dev/null 2>&1 || die "tests failed (run 'make test')"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "==> dry run: would set __version__ = \"${NEW}\", commit, tag ${TAG}"
    echo "    and push ${BRANCH} + ${TAG} to ${REMOTE}"
    exit 0
fi

# ---------------------------------------------------------------- release

sed -i "s/^__version__ = \"${CURRENT}\"/__version__ = \"${NEW}\"/" "$APP"
[ "$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$APP" | head -1)" = "$NEW" ] \
    || die "could not update __version__ in ${APP}"

# build the package with the new version before committing to it
echo "==> building the .deb"
if ! ./packaging/build-deb.sh >/dev/null; then
    git checkout -- "$APP"
    die "the .deb did not build; __version__ restored"
fi

git add "$APP"
git commit --quiet -m "Release ${NEW}"
git tag -a "$TAG" -m "openrouter-credits-tray ${NEW}"
echo "==> committed 'Release ${NEW}' and tagged ${TAG}"

if [ "$PUSH" = ask ]; then
    read -r -p "Push ${BRANCH} and ${TAG} to ${REMOTE} now? This publishes the release. [y/N] " answer
    case "$answer" in [yY]|[yY][eE][sS]) PUSH=yes ;; *) PUSH=no ;; esac
fi

if [ "$PUSH" = yes ]; then
    git push --quiet "$REMOTE" "$BRANCH"
    git push --quiet "$REMOTE" "$TAG"
    echo "==> pushed. GitHub Actions now builds and publishes ${TAG}."
else
    echo "==> not pushed. When ready:"
    echo "    git push ${REMOTE} ${BRANCH} && git push ${REMOTE} ${TAG}"
    echo "    To undo instead: git tag -d ${TAG} && git reset --hard HEAD~1"
fi
