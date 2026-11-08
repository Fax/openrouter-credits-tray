#!/usr/bin/env python3
"""Generate the static app icon (menu/launcher) into packaging/icons/.

Run once; the PNGs are committed so a package build needs no cairo step.

    /usr/bin/python3 packaging/make-icons.py
"""

import importlib.util
import os

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, os.pardir, "openrouter-credits-tray.py")

spec = importlib.util.spec_from_file_location("tray", APP)
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)

GREEN = (0.20, 0.66, 0.33)


def draw(path: str, size: int) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    radius = size * 0.28
    cr.new_sub_path()
    cr.arc(size - radius, radius, radius, -1.5708, 0)
    cr.arc(size - radius, size - radius, radius, 0, 1.5708)
    cr.arc(radius, size - radius, radius, 1.5708, 3.1416)
    cr.arc(radius, radius, radius, 3.1416, 4.7124)
    cr.close_path()
    cr.set_source_rgba(*GREEN, 1.0)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.25)
    cr.set_line_width(max(1.0, size * 0.05))
    cr.stroke()

    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size * 0.78)
    ext = cr.text_extents("$")
    cr.move_to(
        (size - ext.width) / 2 - ext.x_bearing,
        (size - ext.height) / 2 - ext.y_bearing,
    )
    cr.set_source_rgba(1, 1, 1, 1)
    cr.show_text("$")
    surface.write_to_png(path)
    print("wrote", path)


os.makedirs(os.path.join(HERE, "icons"), exist_ok=True)
for px in (48, 256):
    draw(os.path.join(HERE, "icons", "openrouter-credits-tray-%d.png" % px), px)
