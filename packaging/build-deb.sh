#!/usr/bin/env bash
# Build a Debian package (Debian/Ubuntu/Mint).
#
#   ./packaging/build-deb.sh          -> dist/openrouter-credits-tray_<ver>_all.deb
#
# Installs system-wide: /usr/bin/openrouter-credits-tray, a menu entry, a
# system-wide autostart entry (so it starts for every user on login), the app
# icon and the docs. The per-user API key still goes to that user's keyring.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
APP="${ROOT}/openrouter-credits-tray.py"

PKG=openrouter-credits-tray
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${APP}" | head -1)"
[ -n "${VERSION}" ] || { echo "could not read __version__ from ${APP}" >&2; exit 1; }

MAINTAINER="${MAINTAINER:-Fabrizio Rapelli <fabrizio.rapelli@gmail.com>}"
STAGE="$(mktemp -d)"
chmod 0755 "${STAGE}"   # mktemp gives 0700; the archive root should be 0755
OUT="${ROOT}/dist"
DEB="${OUT}/${PKG}_${VERSION}_all.deb"

cleanup() { rm -rf "${STAGE}"; }
trap cleanup EXIT

echo "==> staging ${PKG} ${VERSION}"

install -D -m 0755 "${APP}"                     "${STAGE}/usr/bin/${PKG}"
install -D -m 0644 "${HERE}/${PKG}.desktop"     "${STAGE}/usr/share/applications/${PKG}.desktop"
install -D -m 0644 "${HERE}/${PKG}-autostart.desktop" \
                                                "${STAGE}/etc/xdg/autostart/${PKG}.desktop"
install -D -m 0644 "${HERE}/icons/${PKG}-48.png"  \
                    "${STAGE}/usr/share/icons/hicolor/48x48/apps/${PKG}.png"
install -D -m 0644 "${HERE}/icons/${PKG}-256.png" \
                    "${STAGE}/usr/share/icons/hicolor/256x256/apps/${PKG}.png"
install -D -m 0644 "${ROOT}/README.md"          "${STAGE}/usr/share/doc/${PKG}/README.md"
install -D -m 0644 "${ROOT}/LICENSE"            "${STAGE}/usr/share/doc/${PKG}/copyright"

# Debian policy wants a changelog with every package; generate a minimal one.
# SOURCE_DATE_EPOCH (set by CI from the commit time) keeps the build reproducible.
STAMP="$(date -R -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}")"
cat > "${STAGE}/usr/share/doc/${PKG}/changelog" <<EOF
${PKG} (${VERSION}) unstable; urgency=medium

  * Release ${VERSION}. See https://github.com/Fax/${PKG}/releases

 -- ${MAINTAINER}  ${STAMP}
EOF
gzip -9n "${STAGE}/usr/share/doc/${PKG}/changelog"
chmod 0644 "${STAGE}/usr/share/doc/${PKG}/changelog.gz"

mkdir -p "${STAGE}/DEBIAN"
INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "${STAGE}" | cut -f1)"

cat > "${STAGE}/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: ${MAINTAINER}
Installed-Size: ${INSTALLED_SIZE}
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1
Recommends: gir1.2-secret-1
Homepage: https://github.com/Fax/${PKG}
Description: OpenRouter credits tray indicator
 Shows the remaining OpenRouter account credit in the system tray as a live
 badge, with a menu for usage details and a settings form that stores the API
 key in the desktop keyring (Secret Service).
 .
 Speaks the standard StatusNotifierItem / Ayatana AppIndicator protocol, so it
 works on Cinnamon, XFCE, KDE Plasma, MATE, Budgie, LXQt and GNOME (with the
 AppIndicator extension).
EOF

# refresh the shared icon cache so the menu entry shows its icon immediately
cat > "${STAGE}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
exit 0
EOF
cp "${STAGE}/DEBIAN/postinst" "${STAGE}/DEBIAN/postrm"
chmod 0755 "${STAGE}/DEBIAN/postinst" "${STAGE}/DEBIAN/postrm"

(cd "${STAGE}" && find usr etc -type f -print0 | sort -z | xargs -0 md5sum) \
    > "${STAGE}/DEBIAN/md5sums"
chmod 0644 "${STAGE}/DEBIAN/md5sums"

mkdir -p "${OUT}"
dpkg-deb --root-owner-group --build "${STAGE}" "${DEB}" >/dev/null

echo "==> built ${DEB}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "deb=${DEB}" >> "${GITHUB_OUTPUT}"; fi
echo
echo "Install on any Debian/Ubuntu/Mint machine with:"
echo "    sudo apt install ./$(basename "${DEB}")"
