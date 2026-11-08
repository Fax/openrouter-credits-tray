#!/usr/bin/python3
# Pinned to the system interpreter on purpose: this app needs the distro's
# GI/cairo packages (python3-gi, gir1.2-*), which only exist in /usr/bin/python3.
# `#!/usr/bin/env python3` breaks when a venv, pyenv or conda shim is first on
# PATH — that python3 has no gi/cairo and the tray dies at import.
"""OpenRouter credits tray indicator.

Speaks the standard StatusNotifierItem / Ayatana AppIndicator protocol over
D-Bus, so it works with any host that provides a status-notifier area:
Cinnamon, XFCE, KDE Plasma, MATE, Budgie, LXQt, and GNOME via the AppIndicator
extension. Verified on Cinnamon (Linux Mint 22.3) — see the README for the
panel quirks found there.

Shows the remaining OpenRouter account credit in the system tray, refreshed
periodically. Click for a menu with details (used/total, per-key budget,
last update) plus manual refresh and quit.

Key lookup order:
  1. the desktop keyring (Secret Service: GNOME Keyring / KWallet) — the normal
     place; the Settings form writes there and it is never displayed back
  2. $OPENROUTER_API_KEY in the environment
  3. OPENROUTER_API_KEY= in ~/.config/openrouter-credits/config (fallback for
     machines without a Secret Service)

A plaintext key left in the config file is migrated into the keyring on the
next start and removed from the file. The key is re-read on every refresh, so
adding it later needs no restart.

Requires the system python with these distro packages:
    python3-gi  gir1.2-ayatanaappindicator3-0.1  gir1.2-secret-1
    (gir1.2-secret-1 is optional — without it, use the env/config fallback)

Run:
    /usr/bin/python3 openrouter-credits-tray.py
    /usr/bin/python3 openrouter-credits-tray.py --settings
    /usr/bin/python3 openrouter-credits-tray.py --check
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

try:  # Secret Service API (GNOME Keyring / KWallet) — the desktop standard
    gi.require_version("Secret", "1")
    from gi.repository import Secret  # noqa: E402

    HAVE_SECRET = True
except (ValueError, ImportError):  # pragma: no cover - depends on the host
    Secret = None  # type: ignore[assignment]
    HAVE_SECRET = False

APP_ID = "openrouter-credits"
__version__ = "1.0.0"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"
USAGE_PAGE = "https://openrouter.ai/settings/credits"

# Icons are written as uniquely-named PNGs into the user's hicolor theme so the
# panel resolves them by name through the normal icon lookup (Cinnamon ignores
# the indicator's IconThemePath, so a custom theme dir shows a broken icon).
ICON_DIR = Path.home() / ".local/share" / "icons" / "hicolor"
ICON_THEME = "openrouter-credits"
ICON_SIZES = (16, 22, 24, 32, 48)

CONFIG_DIR = Path.home() / ".config" / APP_ID
CONFIG_FILE = CONFIG_DIR / "config"


def parse_env_text(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines; '#' comments and blank lines ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        if name:
            values[name] = value
    return values


def read_config_file() -> dict[str, str]:
    try:
        return parse_env_text(CONFIG_FILE.read_text())
    except OSError:
        return {}


CONFIG_TEMPLATE = """\
# OpenRouter credits tray — configuration
#
# The API key is NOT stored here. It lives in the desktop keyring (Secret
# Service: GNOME Keyring / KWallet), written by the tray's Settings form.
#
# Optional: how often to refresh, in seconds (minimum 30).
# OPENROUTER_TRAY_INTERVAL=300
"""

KEY_ATTRS = {"account": "openrouter-api-key"}


def _secret_schema():
    if not HAVE_SECRET:
        return None
    return Secret.Schema.new(
        APP_ID,
        Secret.SchemaFlags.NONE,
        {"account": Secret.SchemaAttributeType.STRING},
    )


def _secret_service():
    """Open the Secret Service with collections loaded."""
    if not HAVE_SECRET:
        return None
    flags = Secret.ServiceFlags.LOAD_COLLECTIONS | Secret.ServiceFlags.OPEN_SESSION
    try:
        return Secret.Service.get_sync(flags, None)
    except GLib.Error:
        return None


def _target_collection(svc):
    """Prefer the 'Login' collection: PAM unlocks it at login.

    Storing into the *default* alias can land the item in a keyring that is
    locked after a reboot, which silently breaks the tray until someone
    unlocks it. The login collection is the one that is reliably unlocked.
    """
    collections = svc.get_collections()
    for collection in collections:
        if collection.get_label().strip().lower() == "login" and not collection.get_locked():
            return collection
    for collection in collections:  # any unlocked collection beats failing
        if not collection.get_locked():
            return collection
    return None


def keyring_store(key: str) -> bool:
    """Store the key in the desktop keyring. Returns True on success."""
    schema = _secret_schema()
    svc = _secret_service()
    if schema is None or svc is None or not key.strip():
        return False
    collection = _target_collection(svc)
    if collection is None:
        return False
    try:
        return bool(
            Secret.password_store_sync(
                schema,
                KEY_ATTRS,
                collection.get_object_path(),
                "OpenRouter API key (credits tray)",
                key.strip(),
                None,
            )
        )
    except GLib.Error:
        return False


def _keyring_items():
    """Every stored item matching our schema, across all collections."""
    schema = _secret_schema()
    svc = _secret_service()
    if schema is None or svc is None:
        return []
    try:
        return list(svc.search_sync(schema, KEY_ATTRS, Secret.SearchFlags.ALL, None))
    except GLib.Error:
        return []


def keyring_lookup() -> str | None:
    """Return the stored key, or None if absent, locked, or unavailable.

    Note: `Item.get_secret()` returns None until the secret is loaded, so
    `load_secret_sync()` must be called first with these GI bindings.
    """
    for item in _keyring_items():
        if item.get_locked():
            continue
        try:
            if item.get_secret() is None:
                item.load_secret_sync(None)
            value = item.get_secret()
        except GLib.Error:
            continue
        if value is None:
            continue
        raw = value.get()
        if raw:
            return raw.decode() if isinstance(raw, bytes) else str(raw)
    return None


def keyring_state() -> str:
    """'ok' | 'locked' | 'empty' | 'unavailable' — for an honest status line."""
    if not HAVE_SECRET:
        return "unavailable"
    svc = _secret_service()
    if svc is None:
        return "unavailable"
    items = _keyring_items()
    if not items:
        return "empty"
    if all(item.get_locked() for item in items):
        return "locked"
    return "ok"


def keyring_clear() -> bool:
    schema = _secret_schema()
    svc = _secret_service()
    if schema is None or svc is None:
        return False
    try:
        return bool(Secret.password_clear_sync(schema, KEY_ATTRS, None))
    except GLib.Error:
        return False


def write_config(config: dict[str, str]) -> Path:
    """Write the (non-secret) config file, mode 600. Never holds the API key."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    lines = [
        "# OpenRouter credits tray — configuration",
        "# Written by: openrouter-credits-tray.py — the API key is NOT here,",
        "# it is stored in the desktop keyring (Secret Service).",
        "",
    ]
    for name, value in config.items():
        if name == "OPENROUTER_API_KEY":  # never write the secret to disk
            continue
        lines.append(f"{name}={value}")
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(CONFIG_FILE, 0o600)
    return CONFIG_FILE


def ensure_config_file() -> Path:
    """Create a commented template if no config exists yet (mode 600)."""
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(CONFIG_DIR, 0o700)
        fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(CONFIG_TEMPLATE)
    return CONFIG_FILE


def migrate_plaintext_key() -> None:
    """Move a key found in the config file into the keyring and blank the file."""
    plaintext = (read_config_file().get("OPENROUTER_API_KEY") or "").strip()
    if not plaintext:
        return
    if keyring_store(plaintext):
        config = read_config_file()
        config.pop("OPENROUTER_API_KEY", None)
        write_config(config)


def resolve_key() -> tuple[str | None, str]:
    """Find the API key, preferring the desktop keyring.

    Order: keyring (Secret Service), $OPENROUTER_API_KEY, the config file.
    Returns (key, human-readable source). When the keyring holds an item but is
    locked, the source says so explicitly instead of failing silently.
    """
    stored = keyring_lookup()
    if stored:
        return stored.strip(), "keyring (Secret Service)"
    locked_note = ""
    if keyring_state() == "locked":
        locked_note = "keyring LOCKED — unlock it, or "
    env_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if env_key:
        return env_key, locked_note + "$OPENROUTER_API_KEY"
    config = read_config_file()
    key = (config.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key, locked_note + str(CONFIG_FILE)
    return None, locked_note.rstrip(", or ") or "not set"


def _interval() -> int:
    raw = read_config_file().get("OPENROUTER_TRAY_INTERVAL") or os.environ.get(
        "OPENROUTER_TRAY_INTERVAL", "300"
    )
    try:
        return max(30, int(raw))
    except (TypeError, ValueError):
        return 300


def api_get(url: str, key: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"{APP_ID}/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def fetch_credits(key: str) -> dict:
    """Return {'remaining', 'total', 'usage', plus optional key budget fields}."""
    out: dict = {}
    data = api_get(CREDITS_URL, key).get("data", {})
    total = float(data.get("total_credits") or 0.0)
    usage = float(data.get("total_usage") or 0.0)
    out["total"] = total
    out["usage"] = usage
    out["remaining"] = total - usage
    try:
        kdata = api_get(KEY_URL, key).get("data", {})
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return out
    budget = kdata.get("effective_budget") or {}
    if kdata.get("limit_remaining") is not None:
        out["key_remaining"] = float(kdata["limit_remaining"])
    if budget.get("limit_usd") is not None:
        out["key_limit"] = float(budget["limit_usd"])
    if budget.get("resets_at"):
        out["key_resets"] = str(budget["resets_at"])
    if kdata.get("limit_reset"):
        out["key_reset_period"] = str(kdata["limit_reset"])
    return out


# ---------------------------------------------------------------- icon drawing

def level_color(remaining: float) -> tuple[float, float, float]:
    if remaining <= 0.5:
        return (0.88, 0.25, 0.25)
    if remaining <= 2.0:
        return (0.90, 0.62, 0.11)
    return (0.20, 0.66, 0.33)


def amount_parts(remaining: float) -> tuple[str, str]:
    """Split the amount into a big whole-dollar part and a small cents part."""
    remaining = max(remaining, 0.0)
    if remaining >= 1000:
        return f"{remaining / 1000:.1f}k", ""
    if remaining >= 100:
        return f"{remaining:.0f}", ""
    whole = int(remaining)
    if remaining >= 10:
        return str(whole), f"{remaining - whole:.1f}"[1:]
    return str(whole), f"{remaining - whole:.2f}"[1:]


def draw_amount(cr: cairo.Context, size: int, remaining: float) -> None:
    """Draw the amount as a price tag: big dollars, small cents, one baseline."""
    whole, cents = amount_parts(remaining)
    prefix = "$" if remaining < 1000 else ""
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

    whole_size = size * 0.64
    small_size = size * 0.38

    def measure(w_text: str, s_text: str, p_text: str, w_size: float, s_size: float):
        cr.set_font_size(w_size)
        w = cr.text_extents(w_text)
        cr.set_font_size(s_size)
        s = cr.text_extents(s_text) if s_text else None
        p = cr.text_extents(p_text) if p_text else None
        total = w.width + (s.width if s else 0) + (p.width if p else 0)
        return w, s, p, total

    w_ext, s_ext, p_ext, total = measure(whole, cents, prefix, whole_size, small_size)
    budget = size * 0.96
    if total > budget:  # shrink everything to fit the badge
        factor = budget / total
        whole_size *= factor
        small_size *= factor
        w_ext, s_ext, p_ext, total = measure(
            whole, cents, prefix, whole_size, small_size
        )

    # The pen origin is the baseline. Centre the big part optically and let the
    # small parts share that baseline (classic price-tag look).
    baseline = (size - w_ext.height) / 2 - w_ext.y_bearing
    x = (size - total) / 2
    cr.set_source_rgba(1, 1, 1, 1)
    for ext, text_value in ((p_ext, prefix), (w_ext, whole), (s_ext, cents)):
        if not text_value:
            continue
        cr.set_font_size(whole_size if text_value == whole else small_size)
        cr.move_to(x - ext.x_bearing, baseline)
        cr.show_text(text_value)
        x += ext.width


def write_icon(remaining: float, nonce: int = 0) -> str:
    """Draw a rounded badge with the amount into the icon theme dir.

    Returns the icon name. The name embeds both the amount and a per-refresh
    nonce: a name the panel has never looked up forces a fresh lookup, whereas
    re-publishing an identical name sends no property change and the panel keeps
    whatever it resolved first.
    """
    name = f"{ICON_THEME}-{int(round(max(remaining, 0) * 100))}-{nonce}"
    r, g, b = level_color(remaining)
    for size in ICON_SIZES:
        path = ICON_DIR / f"{size}x{size}" / "apps" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        radius = size * 0.28
        cr.new_sub_path()
        cr.arc(size - radius, radius, radius, -1.5708, 0)
        cr.arc(size - radius, size - radius, radius, 0, 1.5708)
        cr.arc(radius, size - radius, radius, 1.5708, 3.1416)
        cr.arc(radius, radius, radius, 3.1416, 4.7124)
        cr.close_path()
        cr.set_source_rgba(r, g, b, 1.0)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(max(1.0, size * 0.05))
        cr.stroke()
        draw_amount(cr, size, remaining)
        surface.write_to_png(str(path))
    refresh_icon_cache()
    return name


def refresh_icon_cache() -> None:
    """Regenerate the hicolor cache so the panel sees the new icons.

    A stale icon-theme.cache makes GTK skip scanning the theme directory, so a
    freshly written icon is invisible until the cache is rewritten. This runs
    SYNCHRONOUSLY before the icon name is published: the panel does its lookup
    the moment the name changes, so an async refresh races it and the panel
    renders a "missing image" placeholder. It costs ~25 ms.
    """
    try:
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", "-q", str(ICON_DIR)],
            timeout=20,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class TrayApp:
    def __init__(self) -> None:
        self.key: str | None = None
        self.key_source = "not set"
        self.state: dict = {}
        self.error: str | None = None
        self.updated: float | None = None
        self.icon_names: list[str] = []
        self.nonce = 0
        self.last_remaining: float | None = None
        self.pending_icons: dict[str, float] = {}

        ensure_config_file()
        migrate_plaintext_key()
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        # Register with a stock icon: a name the panel already knows resolves
        # reliably, and the real badge is published a moment later (see
        # _schedule_icon). Publishing a brand-new name in the same instant its
        # file and the theme cache are written makes the panel show a
        # "missing image" placeholder.
        self.indicator = AppIndicator.Indicator.new(
            APP_ID, "dialog-information", AppIndicator.IndicatorCategory.SYSTEM_SERVICES
        )
        self.indicator.set_icon_full("dialog-information", "OpenRouter credits")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_label("…", "")
        self.indicator.set_title("OpenRouter credits")

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)

        self.header = Gtk.MenuItem(label="OpenRouter credits")
        self.header.set_sensitive(False)
        self.menu.append(self.header)
        self.detail = Gtk.MenuItem(label="loading…")
        self.detail.set_sensitive(False)
        self.menu.append(self.detail)
        self.budget = Gtk.MenuItem(label="")
        self.budget.set_sensitive(False)
        self.menu.append(self.budget)
        self.stamp = Gtk.MenuItem(label="")
        self.stamp.set_sensitive(False)
        self.menu.append(self.stamp)

        self.keyinfo = Gtk.MenuItem(label="")
        self.keyinfo.set_sensitive(False)
        self.menu.append(self.keyinfo)

        self.menu.append(Gtk.SeparatorMenuItem())

        refresh = Gtk.MenuItem(label="Refresh now")
        refresh.connect("activate", lambda *_: self.refresh(None))
        self.menu.append(refresh)

        open_page = Gtk.MenuItem(label="Open OpenRouter usage…")
        open_page.connect("activate", lambda *_: self.open_page())
        self.menu.append(open_page)

        settings = Gtk.MenuItem(label="Settings…")
        settings.connect("activate", lambda *_: self.open_settings())
        self.menu.append(settings)

        self.autostart_item = Gtk.CheckMenuItem(label="Start on login")
        self.autostart_item.set_active(autostart_enabled())
        self.autostart_item.connect("activate", self.toggle_autostart)
        self.menu.append(self.autostart_item)

        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        self.menu.append(quit_item)

        self.menu.show_all()
        self.budget.hide()
        self.stamp.hide()

        # Always poll, even without a key: refresh() re-resolves the key every
        # time, so the tray recovers by itself once the key/config appears.
        self.refresh(None)
        GLib.timeout_add_seconds(_interval(), self.refresh, None)

    # ------------------------------------------------------------------ updates

    def open_settings(self) -> None:
        """Open the settings form; on save, re-read the key and refresh."""
        def saved(messages: list[str]) -> None:
            self.key, self.key_source = resolve_key()
            if messages:
                self.detail.set_label(" ".join(messages)[:80])
            self.refresh(None)

        SettingsForm(None, saved)

    def _open_path(self, target: str) -> None:
        try:
            subprocess.Popen(
                ["xdg-open", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def open_page(self) -> None:
        self._open_path(USAGE_PAGE)

    def toggle_autostart(self, item: Gtk.CheckMenuItem) -> None:
        set_autostart(item.get_active())
        item.set_active(autostart_enabled())

    def set_error(self, message: str) -> None:
        self.error = message
        self.indicator.set_label("n/a", "")
        self.nonce += 1
        icon = write_icon(-1.0, self.nonce)
        self.icon_names.append(icon)
        self._prune_icons()
        self.pending_icons[icon] = message
        GLib.timeout_add(350, self._announce_icon, icon)
        self.header.set_label("OpenRouter credits — error")
        self.detail.set_label(message[:80])
        self.keyinfo.set_label(f"Key: {self.key_source}")
        self.stamp.set_label("")
        self.stamp.hide()

    def _publish_icon(self, remaining: float) -> str:
        """Write a fresh icon file, then publish its name a beat later.

        The file and the theme cache are written now; the name is handed to the
        panel ~0.35 s later, once the cache rewrite has settled. Doing both in
        the same instant makes the panel resolve the brand-new name against a
        cache it is still reading and render a "missing image" placeholder.
        """
        self.nonce += 1
        icon = write_icon(remaining, self.nonce)
        self.icon_names.append(icon)
        self._prune_icons()
        self.pending_icons[icon] = f"OpenRouter: ${remaining:.2f} left"
        GLib.timeout_add(350, self._announce_icon, icon)
        return icon

    def _announce_icon(self, icon: str) -> bool:
        tooltip = self.pending_icons.pop(icon, None)
        if tooltip is None:
            return False
        self.indicator.set_icon_full(icon, tooltip)
        return False

    def refresh(self, _user_data) -> bool:
        # Re-resolve on every tick so a key added later (or a Hermes .env that
        # appeared after login) is picked up without a restart.
        self.key, self.key_source = resolve_key()
        if not self.key:
            self.set_error(
                f"No API key. Set it in {CONFIG_FILE} (menu: Edit config)"
            )
            return True
        try:
            self.state = fetch_credits(self.key)
            self.error = None
        except urllib.error.HTTPError as exc:
            self.set_error(f"HTTP {exc.code} from OpenRouter")
            return True
        except (urllib.error.URLError, OSError) as exc:
            self.set_error(f"Network error: {getattr(exc, 'reason', exc)}")
            return True
        except (json.JSONDecodeError, ValueError) as exc:
            self.set_error(f"Bad response: {exc}")
            return True

        remaining = float(self.state.get("remaining", 0.0))
        self.updated = time.time()

        label = f"${remaining:.2f}" if remaining < 100 else f"${remaining:.0f}"
        self.indicator.set_label(label, "")
        self.last_remaining = remaining
        self._publish_icon(remaining)

        self.header.set_label(f"${remaining:.2f} remaining")
        self.detail.set_label(
            f"Used ${self.state.get('usage', 0.0):.2f} "
            f"of ${self.state.get('total', 0.0):.2f} purchased"
        )
        self.indicator.set_title(f"OpenRouter: ${remaining:.2f} remaining")

        parts = []
        if "key_remaining" in self.state:
            budget = f"Key budget: ${self.state['key_remaining']:.2f} left"
            if self.state.get("key_limit") is not None:
                budget += f" of ${self.state['key_limit']:.2f}"
            if self.state.get("key_reset_period"):
                budget += f" ({self.state['key_reset_period']})"
            parts.append(budget)
        if self.state.get("key_resets"):
            try:
                when = datetime.fromisoformat(
                    self.state["key_resets"].replace("Z", "+00:00")
                ).astimezone()
                parts.append(f"Resets {when:%b %d %H:%M}")
            except ValueError:
                pass
        if parts:
            self.budget.set_label("  •  ".join(parts))
            self.budget.show()
        else:
            self.budget.hide()

        self.stamp.set_label(f"Updated {datetime.now():%H:%M:%S}")
        self.stamp.show()
        self.keyinfo.set_label(f"Key: {self.key_source}")
        self.menu.show_all()
        if not parts:
            self.budget.hide()
        return True

    def _prune_icons(self) -> None:
        """Keep only the newest few generated icons, drop the rest."""
        keep = set(self.icon_names[-4:])
        for size in ICON_SIZES:
            apps_dir = ICON_DIR / f"{size}x{size}" / "apps"
            if not apps_dir.is_dir():
                continue
            for png in apps_dir.glob(f"{ICON_THEME}-*.png"):
                if png.stem not in keep:
                    try:
                        png.unlink()
                    except OSError:
                        pass


# ------------------------------------------------------------------- autostart

AUTOSTART_FILE = Path.home() / ".config" / "autostart" / f"{APP_ID}.desktop"
SCRIPT_PATH = Path(__file__).resolve()


def autostart_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def set_autostart(enabled: bool) -> None:
    if enabled:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=OpenRouter Credits Tray\n"
            "Comment=Show remaining OpenRouter credits in the system tray\n"
            f"Exec=/usr/bin/python3 {SCRIPT_PATH} --delay 5\n"
            "X-GNOME-Autostart-enabled=true\n"
            "NoDisplay=false\n"
        )
    else:
        AUTOSTART_FILE.unlink(missing_ok=True)


def wait_for_status_notifier(timeout: float = 20.0) -> bool:
    """Wait until a StatusNotifierWatcher owns its bus name.

    At login the tray app can start before Cinnamon's panel registers the
    watcher; an indicator created that early is never shown. Wait for it.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return False
    deadline = time.monotonic() + timeout
    while True:
        try:
            if bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                GLib.VariantType("(b)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            ).unpack()[0]:
                return True
        except GLib.Error:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


class SettingsForm:
    """A real GTK form for the tray's settings.

    The key is typed into a masked entry and stored in the desktop keyring; it
    is never written to a plain file and never displayed back.
    """

    def __init__(self, parent: Gtk.Window | None, on_saved=None) -> None:
        self.on_saved = on_saved
        self.key, self.source = resolve_key()

        self.dialog = Gtk.Dialog(title="OpenRouter Credits — Settings")
        self.dialog.set_border_width(14)
        self.dialog.set_resizable(False)
        if parent is not None:
            self.dialog.set_transient_for(parent)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.dialog.add_button("Forget stored key", Gtk.ResponseType.REJECT)
        save = self.dialog.add_button("Save", Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")
        self.dialog.set_default_response(Gtk.ResponseType.OK)

        area = self.dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        area.add(grid)

        row = 0
        label = Gtk.Label(label="API key", xalign=0)
        grid.attach(label, 0, row, 1, 1)
        self.entry = Gtk.Entry()
        self.entry.set_visibility(False)
        self.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.entry.set_width_chars(42)
        self.entry.set_placeholder_text(
            "stored — type a new key to replace it" if self.key else "sk-or-v1-…"
        )
        self.entry.set_activates_default(True)
        grid.attach(self.entry, 1, row, 1, 1)
        row += 1

        self.show_toggle = Gtk.CheckButton(label="Show key while typing")
        self.show_toggle.connect("toggled", self._toggle_visibility)
        grid.attach(self.show_toggle, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Refresh interval", xalign=0), 0, row, 1, 1)
        self.spin = Gtk.SpinButton.new_with_range(30, 86400, 30)
        self.spin.set_value(_interval())
        grid.attach(self.spin, 1, row, 1, 1)
        row += 1

        stored = (
            f"Key source: {self.source}"
            if self.key
            else "No key stored yet — paste one above and press Save."
        )
        self.status = Gtk.Label(xalign=0)
        self.status.set_markup(f"<small>{GLib.markup_escape_text(stored)}</small>")
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(60)
        grid.attach(self.status, 0, row, 2, 1)

        hint = Gtk.Label(xalign=0)
        hint.set_markup(
            "<small>The key is kept in the desktop keyring (Secret Service), "
            "not in a plain file.</small>"
        )
        grid.attach(hint, 0, row + 1, 2, 1)

        self.dialog.connect("response", self._on_response)
        self.dialog.show_all()

    def _toggle_visibility(self, button: Gtk.CheckButton) -> None:
        self.entry.set_visibility(button.get_active())

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            typed = self.entry.get_text().strip()
            messages = []
            if typed:
                if keyring_store(typed):
                    messages.append("API key saved to the keyring.")
                else:
                    messages.append(
                        "Could not save the key to the keyring — is the keyring "
                        "unlocked?"
                    )
            config = read_config_file()
            config.pop("OPENROUTER_API_KEY", None)  # never keep a plaintext copy
            config["OPENROUTER_TRAY_INTERVAL"] = str(int(self.spin.get_value()))
            write_config(config)
            if self.on_saved is not None:
                self.on_saved(messages)
        elif response == Gtk.ResponseType.REJECT:
            confirm = Gtk.MessageDialog(
                transient_for=dialog,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text="Remove the stored OpenRouter key from the keyring?",
            )
            confirm.format_secondary_text(
                "The tray will stop showing a balance until a new key is saved."
            )
            if confirm.run() == Gtk.ResponseType.OK:
                keyring_clear()
                config = read_config_file()
                config.pop("OPENROUTER_API_KEY", None)
                write_config(config)
                if self.on_saved is not None:
                    self.on_saved(["Stored key removed from the keyring."])
            confirm.destroy()
        dialog.destroy()


def open_settings_form(parent=None, on_saved=None) -> None:
    SettingsForm(parent, on_saved)


def main() -> int:
    args = sys.argv[1:]
    if "--version" in args or "-V" in args:
        print(f"openrouter-credits-tray {__version__}")
        return 0
    if "--help" in args or "-h" in args:
        print(__doc__)
        print(
            "Options:\n"
            "  --settings        open the Settings form (API key → keyring) and exit\n"
            "  --set-key [KEY]   store KEY in the keyring (prompts when omitted) and exit\n"
            "  --clear-key       remove the stored key from the keyring and exit\n"
            "  --check           print the key source and a live balance, then exit\n"
            "  --version         print the version and exit\n"
            "  --delay <secs>    wait before creating the tray icon (login startup)\n"
        )
        return 0

    if "--set-key" in args:
        index = args.index("--set-key")
        value = args[index + 1] if index + 1 < len(args) else ""
        if not value:
            # Read from the terminal instead of argv so the key never shows up
            # in the process list.
            value = getpass.getpass("OpenRouter API key: ").strip()
        if not value:
            print("no key given", file=sys.stderr)
            return 2
        if not keyring_store(value):
            print("could not store the key (is the keyring unlocked?)", file=sys.stderr)
            return 1
        config = read_config_file()
        config.pop("OPENROUTER_API_KEY", None)
        write_config(config)
        print("Key stored in the desktop keyring.")
        return 0

    if "--clear-key" in args:
        if keyring_clear():
            print("Stored key removed from the keyring.")
        else:
            print("No stored key to remove.", file=sys.stderr)
        return 0

    if "--settings" in args:
        open_settings_form(None, lambda messages: print(" ".join(messages)))
        Gtk.main()
        return 0

    if "--check" in args:
        key, source = resolve_key()
        if not key:
            print("No API key found.")
            print(f"Set one with: {SCRIPT_PATH} --settings")
            return 1
        print(f"Key source: {source}")
        try:
            state = fetch_credits(key)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"API call failed: {exc}")
            return 1
        print(
            f"Remaining ${state.get('remaining', 0):.2f} "
            f"(used ${state.get('usage', 0):.2f} of ${state.get('total', 0):.2f})"
        )
        return 0

    delay = 0.0
    if "--delay" in args:
        index = args.index("--delay")
        if index + 1 < len(args):
            try:
                delay = max(0.0, float(args[index + 1]))
            except ValueError:
                delay = 0.0
    if delay:
        time.sleep(delay)
    wait_for_status_notifier()

    TrayApp()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
