# openrouter-credits-tray

A tiny Linux system-tray indicator that shows your remaining **OpenRouter**
credit as a live badge, so you can see the balance drop while you work instead
of opening the dashboard.

![the tray badge](docs/tray-badge.png)

Left-click the badge for the detail menu, including the Settings form:

![the settings form](docs/settings-form.png)

## What it does

- Shows the remaining credit **inside the icon** as a price tag (`$3.04`), colour
  coded: green above $2, amber above $0.50, red below.
- Refreshes every 5 minutes (configurable), and on demand from the menu.
- Menu shows: remaining, `used / purchased`, the per-key budget and its reset
  date, the last update time, and which key source is in use.
- **Settings form** to paste the API key and set the refresh interval.
- Stores the key in the **desktop keyring** (Secret Service: GNOME Keyring,
  KWallet). It is never written to a config file and never displayed back.
- Optional start-on-login, and a systemd user unit if you prefer that.

## Requirements

A Linux desktop with a StatusNotifier/appindicator tray (Cinnamon, XFCE, KDE,
GNOME with an extension, …), and the **system** Python:

```bash
# Debian / Ubuntu / Mint
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-secret-1

# Fedora
sudo dnf install python3-gobject libayatana-appindicator-gtk3 libsecret

# Arch
sudo pacman -S python-gobject libayatana-appindicator libsecret
```

`gir1.2-secret-1` is optional — without it the keyring is unavailable and the
tray falls back to `$OPENROUTER_API_KEY` or the config file.

## Install

```bash
git clone https://github.com/<you>/openrouter-credits-tray.git
cd openrouter-credits-tray
./install.sh              # copies the script to ~/.local/bin and starts it
```

Then open the tray menu → **Settings…** and paste an OpenRouter API key
(create one at <https://openrouter.ai/settings/keys>). Save — the key goes
straight into your keyring.

`install.sh` flags:

| Flag | Effect |
|---|---|
| `--autostart` | also start it on login (writes `~/.config/autostart/…desktop`) |
| `--systemd` | install and enable a systemd **user** unit instead |
| `--no-start` | install only, don't launch |

## Usage

```bash
openrouter-credits-tray.py              # run the tray
openrouter-credits-tray.py --settings   # open the Settings form
openrouter-credits-tray.py --check      # print the key source + a live balance
openrouter-credits-tray.py --set-key    # store a key (hidden prompt, no shell history)
openrouter-credits-tray.py --clear-key  # remove the stored key
```

Environment variables:

- `OPENROUTER_TRAY_INTERVAL` — refresh seconds (default `300`, minimum `30`).

## Where things live

| Path | What |
|---|---|
| `~/.config/openrouter-credits/config` | non-secret settings (refresh interval) |
| `~/.config/autostart/openrouter-credits.desktop` | start-on-login entry, when enabled |
| `~/.local/share/icons/hicolor/*/apps/openrouter-credits-*.png` | generated badge icons |

## Key handling

Lookup order: **keyring → `$OPENROUTER_API_KEY` → config file**. A plaintext key
found in the config file is migrated into the keyring on the next start and
removed from the file.

Be clear about what the keyring buys you:

- **It does** keep the key out of a plaintext file: an unencrypted disk, a
  backup, or a stray file read no longer yields it.
- **It does not** protect against other processes running as *you*. While the
  keyring is unlocked, any of your processes can ask the daemon for the secret —
  Linux keyrings have no per-application ACL.
- If the keyring is locked (or the daemon is missing), the menu says so
  explicitly — `Key: keyring LOCKED — unlock it, or …` — rather than silently
  falling back.

The tray talks only to `openrouter.ai/api/v1/credits` and `/api/v1/key`, with the
key in an `Authorization: Bearer` header over TLS. It writes nothing else.

## Troubleshooting

**The tray icon is a broken-image placeholder.** The panel resolves icon names
through the GTK icon theme, which is backed by `icon-theme.cache`. This app
writes its badge into `~/.local/share/icons/hicolor/<size>/apps/` and regenerates
that cache (`gtk-update-icon-cache -f -t`) after every write — without it, a
stale cache makes brand-new icon files invisible. If you still see a placeholder,
the cache in *your* theme is stale; run:

```bash
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

**The icon shows on the next refresh but not immediately.** Publishing a brand-new
icon name in the same instant its file and the cache are written can make the
panel resolve it against a cache it is still reading. The app avoids this by
writing the file first and announcing the name ~350 ms later. Nothing to do.

**`n/a` in the badge and an error in the menu.** Read the menu's `Key:` line:

- `not set` → no key anywhere; open **Settings…**.
- `keyring LOCKED …` → unlock your keyring (on Mint/Cinnamon the *Login* keyring
  is unlocked by PAM at login; a keyring with a different password stays locked).
- `HTTP 401` → the key is wrong or revoked.
- `Network error` → no connectivity to `openrouter.ai`.

**Nothing appears in the tray at all.** Confirm your panel has a status-notifier
area, and that `org.kde.StatusNotifierWatcher` is on the session bus:

```bash
gdbus call --session --dest org.kde.StatusNotifierWatcher --object-path /StatusNotifierWatcher \
  --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
```

A tray item can linger in the panel after its process dies; restarting
`xapp-sn-watcher` (Mint) clears stale entries without touching the shell.

**Start-on-login doesn't fire.** The app can start before the panel registers the
watcher; it waits for the watcher before creating the icon, and the autostart
entry also passes `--delay 5`.

## Uninstall

```bash
openrouter-credits-tray.py --clear-key       # remove the stored key from the keyring
rm ~/.local/bin/openrouter-credits-tray.py
rm ~/.config/autostart/openrouter-credits.desktop   # if enabled
rm -rf ~/.config/openrouter-credits
rm ~/.local/share/icons/hicolor/*/apps/openrouter-credits-*.png
```

## Development

```bash
python3 -m unittest discover -s tests -v     # pure-function tests, no display needed
```

The single file is the whole app — no packaging, no build step. It needs
`python3-gi` at import time, so tests run with the system Python.

## License

MIT — see [LICENSE](LICENSE).
