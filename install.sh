#!/usr/bin/env bash
# Installer for openrouter-credits-tray.
#
#   ./install.sh                 install to ~/.local/bin and start it
#   ./install.sh --autostart     also start it on login
#   ./install.sh --systemd       install and enable a systemd user unit
#   ./install.sh --no-start      install only
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
TARGET="${BIN_DIR}/openrouter-credits-tray.py"
AUTOSTART_DIR="${HOME}/.config/autostart"
UNIT_DIR="${HOME}/.config/systemd/user"
PY="/usr/bin/python3"

AUTOSTART=0
SYSTEMD=0
START=1

for arg in "$@"; do
    case "$arg" in
        --autostart) AUTOSTART=1 ;;
        --systemd) SYSTEMD=1 ;;
        --no-start) START=0 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "==> checking runtime dependencies"
missing=0
"${PY}" - <<'PYCHECK' || missing=1
import sys
missing = []
for ns, version, hint in (
    ("Gtk", "3.0", "python3-gi"),
    ("AyatanaAppIndicator3", "0.1", "gir1.2-ayatanaappindicator3-0.1"),
):
    try:
        import gi
        gi.require_version(ns, version)
    except (ValueError, ImportError):
        missing.append(hint)
if missing:
    print("missing packages: " + ", ".join(missing))
    sys.exit(1)
try:
    import gi
    gi.require_version("Secret", "1")
except (ValueError, ImportError):
    print("note: gir1.2-secret-1 not installed — keyring storage disabled,")
    print("      the tray will use $OPENROUTER_API_KEY or the config file instead")
PYCHECK
if [ "$missing" -ne 0 ]; then
    echo "Install the packages above first (see README), then re-run." >&2
    exit 1
fi

echo "==> installing to ${TARGET}"
mkdir -p "${BIN_DIR}"
install -m 0755 "${HERE}/openrouter-credits-tray.py" "${TARGET}"

if [ "$AUTOSTART" -eq 1 ]; then
    echo "==> enabling start on login"
    mkdir -p "${AUTOSTART_DIR}"
    cat > "${AUTOSTART_DIR}/openrouter-credits.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=OpenRouter Credits Tray
Comment=Show remaining OpenRouter credits in the system tray
Exec=${PY} ${TARGET} --delay 5
X-GNOME-Autostart-enabled=true
NoDisplay=false
EOF
fi

if [ "$SYSTEMD" -eq 1 ]; then
    echo "==> installing systemd user unit"
    mkdir -p "${UNIT_DIR}"
    sed "s|@PY@|${PY}|; s|@TARGET@|${TARGET}|" \
        "${HERE}/packaging/openrouter-credits-tray.service" \
        > "${UNIT_DIR}/openrouter-credits-tray.service"
    systemctl --user daemon-reload
    systemctl --user enable --now openrouter-credits-tray.service
    echo "==> enabled; check it with: systemctl --user status openrouter-credits-tray"
    START=0
fi

if [ "$START" -eq 1 ]; then
    if pgrep -f "${TARGET}" >/dev/null 2>&1; then
        echo "==> already running"
    else
        echo "==> starting (detached from this shell)"
        systemd-run --user --unit=openrouter-credits-tray --collect \
            --description="OpenRouter credits tray" "${PY}" "${TARGET}" \
            >/dev/null 2>&1 || {
                setsid "${PY}" "${TARGET}" >/dev/null 2>&1 &
            }
    fi
fi

echo
echo "Done. Open the tray menu and pick 'Settings…' to paste your OpenRouter API key."
