#!/usr/bin/env python3
"""Unit tests for the pure helpers in openrouter-credits-tray.

No display, no keyring and no network needed:

    python3 -m unittest discover -s tests -v
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "openrouter-credits-tray.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tray_under_test", APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tray_under_test"] = module
    spec.loader.exec_module(module)
    return module


tray = load_module()


class TestAmountParts(unittest.TestCase):
    def test_under_ten_keeps_cents(self):
        self.assertEqual(tray.amount_parts(3.08), ("3", ".08"))
        self.assertEqual(tray.amount_parts(0.42), ("0", ".42"))

    def test_ten_to_hundred_one_decimal(self):
        self.assertEqual(tray.amount_parts(12.34), ("12", ".3"))
        self.assertEqual(tray.amount_parts(47.5), ("47", ".5"))

    def test_large_amounts_drop_the_fraction(self):
        self.assertEqual(tray.amount_parts(128.0), ("128", ""))
        self.assertEqual(tray.amount_parts(1500.0), ("1.5k", ""))

    def test_negative_clamps_to_zero(self):
        self.assertEqual(tray.amount_parts(-1.0), ("0", ".00"))


class TestLevelColor(unittest.TestCase):
    def test_thresholds(self):
        green = tray.level_color(5.0)
        amber = tray.level_color(1.5)
        red = tray.level_color(0.2)
        self.assertEqual(len(green), 3)
        self.assertNotEqual(green, amber)
        self.assertNotEqual(amber, red)

    def test_boundaries(self):
        self.assertEqual(tray.level_color(2.01), tray.level_color(100.0))
        self.assertEqual(tray.level_color(0.5), tray.level_color(0.0))


class TestParseEnvText(unittest.TestCase):
    def test_ignores_comments_and_blanks(self):
        parsed = tray.parse_env_text("# c\n\nA=1\nB = 2 \n")
        self.assertEqual(parsed, {"A": "1", "B": "2"})

    def test_strips_quotes(self):
        self.assertEqual(tray.parse_env_text("K='v'\n"), {"K": "v"})
        self.assertEqual(tray.parse_env_text('K="v"\n'), {"K": "v"})

    def test_skips_lines_without_equals(self):
        self.assertEqual(tray.parse_env_text("nonsense\nK=v\n"), {"K": "v"})


class TestKeyResolutionOrder(unittest.TestCase):
    """The keyring is tried first, then the env var, then the config file."""

    def setUp(self):
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "orcredits-test-config"
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self._config_file = tray.CONFIG_FILE
        self._lookup = tray.keyring_lookup
        self._state = tray.keyring_state
        self._env = os.environ.pop("OPENROUTER_API_KEY", None)

    def tearDown(self):
        tray.CONFIG_FILE = self._config_file
        tray.keyring_lookup = self._lookup
        tray.keyring_state = self._state
        if self._env is not None:
            os.environ["OPENROUTER_API_KEY"] = self._env
        if self.tmp.exists():
            self.tmp.unlink()

    def test_keyring_wins(self):
        tray.keyring_lookup = lambda: "from-keyring"
        tray.keyring_state = lambda: "ok"
        os.environ["OPENROUTER_API_KEY"] = "from-env"
        tray.CONFIG_FILE = self.tmp
        self.tmp.write_text("OPENROUTER_API_KEY=from-file\n")
        self.assertEqual(tray.resolve_key(), ("from-keyring", "keyring (Secret Service)"))

    def test_env_beats_config_file(self):
        tray.keyring_lookup = lambda: None
        tray.keyring_state = lambda: "empty"
        os.environ["OPENROUTER_API_KEY"] = "from-env"
        tray.CONFIG_FILE = self.tmp
        self.tmp.write_text("OPENROUTER_API_KEY=from-file\n")
        self.assertEqual(tray.resolve_key(), ("from-env", "$OPENROUTER_API_KEY"))

    def test_config_file_is_the_last_resort(self):
        tray.keyring_lookup = lambda: None
        tray.keyring_state = lambda: "empty"
        os.environ.pop("OPENROUTER_API_KEY", None)
        tray.CONFIG_FILE = self.tmp
        self.tmp.write_text("OPENROUTER_API_KEY=from-file\n")
        self.assertEqual(tray.resolve_key()[0], "from-file")

    def test_locked_keyring_is_reported_not_hidden(self):
        tray.keyring_lookup = lambda: None
        tray.keyring_state = lambda: "locked"
        os.environ.pop("OPENROUTER_API_KEY", None)
        tray.CONFIG_FILE = self.tmp
        self.tmp.write_text("OPENROUTER_API_KEY=from-file\n")
        _, source = tray.resolve_key()
        self.assertIn("LOCKED", source)

    def test_nothing_available(self):
        tray.keyring_lookup = lambda: None
        tray.keyring_state = lambda: "empty"
        os.environ.pop("OPENROUTER_API_KEY", None)
        tray.CONFIG_FILE = self.tmp  # does not exist
        self.assertEqual(tray.resolve_key(), (None, "not set"))


class TestInterval(unittest.TestCase):
    def setUp(self):
        self._config_file = tray.CONFIG_FILE
        self._env = os.environ.pop("OPENROUTER_TRAY_INTERVAL", None)
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "orcredits-test-interval"

    def tearDown(self):
        tray.CONFIG_FILE = self._config_file
        if self._env is not None:
            os.environ["OPENROUTER_TRAY_INTERVAL"] = self._env
        if self.tmp.exists():
            self.tmp.unlink()

    def test_default(self):
        tray.CONFIG_FILE = self.tmp
        self.assertEqual(tray._interval(), 300)

    def test_env_override(self):
        tray.CONFIG_FILE = self.tmp
        os.environ["OPENROUTER_TRAY_INTERVAL"] = "600"
        self.assertEqual(tray._interval(), 600)

    def test_config_file_override(self):
        tray.CONFIG_FILE = self.tmp
        self.tmp.write_text("OPENROUTER_TRAY_INTERVAL=900\n")
        self.assertEqual(tray._interval(), 900)

    def test_floor_and_garbage(self):
        tray.CONFIG_FILE = self.tmp
        os.environ["OPENROUTER_TRAY_INTERVAL"] = "5"
        self.assertEqual(tray._interval(), 30)
        os.environ["OPENROUTER_TRAY_INTERVAL"] = "not-a-number"
        self.assertEqual(tray._interval(), 300)


class TestAutostart(unittest.TestCase):
    """The user entry overrides the packaged /etc/xdg one by sharing its name."""

    def setUp(self):
        import tempfile

        self._saved = (
            tray.AUTOSTART_FILE,
            tray.LEGACY_AUTOSTART_FILE,
            tray.SYSTEM_AUTOSTART_FILE,
        )
        self.dir = Path(tempfile.mkdtemp())
        tray.AUTOSTART_FILE = self.dir / "user" / "openrouter-credits-tray.desktop"
        tray.LEGACY_AUTOSTART_FILE = self.dir / "user" / "openrouter-credits.desktop"
        tray.SYSTEM_AUTOSTART_FILE = self.dir / "etc" / "openrouter-credits-tray.desktop"

    def tearDown(self):
        import shutil

        (
            tray.AUTOSTART_FILE,
            tray.LEGACY_AUTOSTART_FILE,
            tray.SYSTEM_AUTOSTART_FILE,
        ) = self._saved
        shutil.rmtree(self.dir)

    def _install_system_entry(self):
        tray.SYSTEM_AUTOSTART_FILE.parent.mkdir(parents=True)
        tray.SYSTEM_AUTOSTART_FILE.write_text("[Desktop Entry]\nType=Application\n")

    def test_user_install_toggle(self):
        self.assertFalse(tray.autostart_enabled())
        tray.set_autostart(True)
        self.assertTrue(tray.autostart_enabled())
        self.assertIn("--delay 5", tray.AUTOSTART_FILE.read_text())
        tray.set_autostart(False)
        self.assertFalse(tray.autostart_enabled())
        self.assertFalse(tray.AUTOSTART_FILE.exists())

    def test_package_entry_is_overridden_not_duplicated(self):
        self._install_system_entry()
        self.assertTrue(tray.autostart_enabled())
        tray.set_autostart(False)
        self.assertFalse(tray.autostart_enabled())
        self.assertIn("Hidden=true", tray.AUTOSTART_FILE.read_text())
        tray.set_autostart(True)
        self.assertTrue(tray.autostart_enabled())
        self.assertFalse(tray.AUTOSTART_FILE.exists())

    def test_legacy_file_counts_and_is_cleaned_up(self):
        tray.LEGACY_AUTOSTART_FILE.parent.mkdir(parents=True)
        tray.LEGACY_AUTOSTART_FILE.write_text("[Desktop Entry]\n")
        self.assertTrue(tray.autostart_enabled())
        tray.set_autostart(False)
        self.assertFalse(tray.LEGACY_AUTOSTART_FILE.exists())
        self.assertFalse(tray.autostart_enabled())


if __name__ == "__main__":
    unittest.main()
