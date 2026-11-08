#!/usr/bin/env bash
# Build an RPM package (Fedora, RHEL and derivatives).
#
#   ./packaging/build-rpm.sh          -> dist/openrouter-credits-tray-<ver>-1.<dist>.noarch.rpm
#
# Needs rpm-build and desktop-file-utils. Installs the same files as the .deb:
# /usr/bin/openrouter-credits-tray, a menu entry, a system-wide autostart entry,
# the app icon, the man page and the docs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
APP="${ROOT}/openrouter-credits-tray.py"

PKG=openrouter-credits-tray
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${APP}" | head -1)"
[ -n "${VERSION}" ] || { echo "could not read __version__ from ${APP}" >&2; exit 1; }
command -v rpmbuild >/dev/null || { echo "rpmbuild not found: sudo dnf install rpm-build" >&2; exit 1; }

MAINTAINER="${MAINTAINER:-Fabrizio Rapelli <fabrizio.rapelli@gmail.com>}"
TOPDIR="$(mktemp -d)"
OUT="${ROOT}/dist"

cleanup() { rm -rf "${TOPDIR}"; }
trap cleanup EXIT

echo "==> building ${PKG} ${VERSION} rpm"

make -C "${ROOT}" --no-print-directory -B dist >/dev/null
mkdir -p "${TOPDIR}/SOURCES" "${TOPDIR}/SPECS"
cp "${OUT}/${PKG}-${VERSION}.tar.gz" "${TOPDIR}/SOURCES/"

# SOURCE_DATE_EPOCH (set by CI from the commit time) keeps the build reproducible.
STAMP="$(LC_ALL=C date -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}" '+%a %b %d %Y')"
SPEC="${TOPDIR}/SPECS/${PKG}.spec"
sed -e "s/@VERSION@/${VERSION}/g" \
    -e "s/@DATE@/${STAMP}/g" \
    -e "s/@MAINTAINER@/${MAINTAINER}/g" \
    "${HERE}/${PKG}.spec.in" > "${SPEC}"

rpmbuild --quiet -ba --define "_topdir ${TOPDIR}" "${SPEC}"

mkdir -p "${OUT}"
cp "${TOPDIR}"/RPMS/noarch/*.rpm "${TOPDIR}"/SRPMS/*.src.rpm "${OUT}/"
RPM="$(ls "${OUT}/${PKG}-${VERSION}"-*.noarch.rpm | tail -1)"

echo "==> built ${RPM}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "rpm=${RPM}" >> "${GITHUB_OUTPUT}"; fi
echo
echo "Install on Fedora with:"
echo "    sudo dnf install ./$(basename "${RPM}")"
