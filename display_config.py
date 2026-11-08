"""
display_config.py  —  load and check ProtoFace display configs (display.json)

The format is documented in the ProtoFace repo, docs/display-config.md.
This module has no GUI and no third-party dependencies, so the firmware tests,
the AnimFile Maker and command-line checks can all share it.

    python display_config.py displays/*.json      # check files, print a summary
"""

import json
import os
import struct
import sys
import zlib

FORMAT_VERSION = 1
MAX_PIXELS = 4096

OUTPUT_TYPES = ('ws2812', 'hub75', 'max7219')
WIRINGS = ('rows', 'serpentine', 'columns', 'columns_serpentine')
STARTS = ('top-left', 'top-right', 'bottom-left', 'bottom-right')
FLIPS = ('none', 'h', 'v', 'hv')
ROTATES = (0, 90, 180, 270)

# Presets ship in displays/ next to this file (or inside a PyInstaller bundle).
PRESET_DIR = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                          'displays')


class ConfigError(ValueError):
    """A display config that cannot be used.  The message says what to fix."""


# ── Data classes ──────────────────────────────────────────────────────────────
class Panel:
    def __init__(self, d, offset):
        self.id = d['id']
        self.w = d['w']
        self.h = d['h']
        self.x = d['x']
        self.y = d['y']
        self.side = d.get('side')
        self.region = d.get('region')
        self.custom = 'pixels' in d
        if self.custom:
            self.pixels = [tuple(p) for p in d['pixels']]
        else:
            self.pixels = [(x, y) for y in range(self.h) for x in range(self.w)]
        self.offset = offset            # index of this panel's first pixel in a frame
        self._index = {p: i for i, p in enumerate(self.pixels)}

    @property
    def count(self):
        return len(self.pixels)

    def index_of(self, x, y):
        """Pixel index inside this panel for (x, y), or None if no LED is there."""
        return self._index.get((x, y))


class DisplayConfig:
    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.name = data['name']
        self.description = data.get('description', '')
        self.panels = []
        offset = 0
        for d in data['panels']:
            p = Panel(d, offset)
            self.panels.append(p)
            offset += p.count
        self.total_pixels = offset
        self.by_id = {p.id: p for p in self.panels}
        self.mirror = data.get('mirror')
        self.outputs = data['outputs']
        self.limits = data.get('limits', {})

    # Canvas size the painter needs: the bounding box of all panels.
    @property
    def canvas_size(self):
        return (max(p.x + p.w for p in self.panels),
                max(p.y + p.h for p in self.panels))

    @property
    def sides(self):
        """Side names in first-seen order (panels without a side are left out)."""
        out = []
        for p in self.panels:
            if p.side is not None and p.side not in out:
                out.append(p.side)
        return out

    def regions(self, side=None):
        """{region: [panels]} in panel order, optionally for one side."""
        out = {}
        for p in self.panels:
            if p.region is None or (side is not None and p.side != side):
                continue
            out.setdefault(p.region, []).append(p)
        return out

    def layout_id(self):
        return layout_id(self.panels)

    def is_v1_compatible(self, v1_panel_count):
        """True when a version-1 .anim with this panel count fits this config."""
        return (len(self.panels) == v1_panel_count and
                all(not p.custom and p.w == 8 and p.h == 8 for p in self.panels))

    def mirror_map(self, flip=None):
        """
        {flat index: flat index on the other side} for "Mirror to other side",
        in both directions.
        Panels are matched by region, then by order within the region; with
        flip 'h' the order within a region is reversed and pixels are mirrored.
        """
        if not self.mirror:
            return {}
        flip = self.mirror.get('flip', 'none') if flip is None else flip
        src = self.regions(self.mirror['from'])
        dst = self.regions(self.mirror['to'])
        out = {}
        for region, sp in src.items():
            dp = dst.get(region, [])
            if flip == 'h':
                dp = list(reversed(dp))
            for a, b in zip(sp, dp):
                for i, (x, y) in enumerate(a.pixels):
                    tx = (b.w - 1 - x) if flip == 'h' else x
                    j = b.index_of(tx, y)
                    if j is not None:
                        out[a.offset + i] = b.offset + j
                        out[b.offset + j] = a.offset + i
        return out


# ── Drawing geometry ──────────────────────────────────────────────────────────
def _bands(intervals, join_touching):
    """Merge [start, end) intervals into sorted, non-overlapping bands."""
    bands = []
    for a, b in sorted(intervals):
        if bands and (a < bands[-1][1] or (join_touching and a == bands[-1][1])):
            bands[-1][1] = max(bands[-1][1], b)
        else:
            bands.append([a, b])
    return bands


class CanvasGeometry:
    """
    Where each panel is drawn on screen for an LED cell size.  Panel x/y come
    from the config, in pixels; rows (and columns) of panels that do not
    overlap get extra space between them for region outlines and labels.
    """

    def __init__(self, cfg, cell, gap, label_h):
        self.cfg, self.cell = cfg, cell
        # Side-by-side panels stay together; a row of panels directly under
        # another gets room for the lower row's labels.
        self._xb = _bands(((p.x, p.x + p.w) for p in cfg.panels), join_touching=True)
        self._yb = _bands(((p.y, p.y + p.h) for p in cfg.panels), join_touching=False)
        self._gap, self._label_h = gap, label_h
        last_x, last_y = self._xb[-1][1], self._yb[-1][1]
        self.width = self._sx(last_x, len(self._xb) - 1) + gap
        self.height = self._sy(last_y, len(self._yb) - 1) + gap

    def _sx(self, v, k):
        return self._gap + v * self.cell + k * self._gap

    def _sy(self, v, k):
        return self._gap + self._label_h + v * self.cell + k * (self._gap + self._label_h)

    @staticmethod
    def _band(bands, v):
        return next(i for i, (a, b) in enumerate(bands) if a <= v < b)

    def origin(self, panel):
        """Screen position of the panel's top-left pixel."""
        return (self._sx(panel.x, self._band(self._xb, panel.x)),
                self._sy(panel.y, self._band(self._yb, panel.y)))

    def rects(self):
        """{flat pixel index: (x1, y1, x2, y2)} for every pixel of the display."""
        out, c = {}, self.cell
        for p in self.cfg.panels:
            ox, oy = self.origin(p)
            for i, (x, y) in enumerate(p.pixels):
                x1, y1 = ox + x * c, oy + y * c
                out[p.offset + i] = (x1, y1, x1 + c - 1, y1 + c - 1)
        return out


# ── Layout ID ─────────────────────────────────────────────────────────────────
def layout_id(panels):
    """CRC-32 over each panel's w, h, pixel count (uint16 LE) and custom pixel positions."""
    buf = bytearray()
    for p in panels:
        buf += struct.pack('<HHH', p.w, p.h, p.count)
        if p.custom:
            for x, y in p.pixels:
                buf += bytes((x, y))
    return zlib.crc32(bytes(buf)) & 0xFFFFFFFF


# ── Loading and checking ──────────────────────────────────────────────────────
def _need(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _entry(e, where):
    """Normalise a chain/place entry to a dict with 'panel'."""
    if isinstance(e, str):
        return {'panel': e}
    _need(isinstance(e, dict) and isinstance(e.get('panel'), str),
          f"{where}: each entry must be a panel id or an object with \"panel\"")
    _need(e.get('flip', 'none') in FLIPS, f"{where}: flip must be one of {FLIPS}")
    _need(e.get('rotate', 0) in ROTATES, f"{where}: rotate must be one of {ROTATES}")
    _need(e.get('wiring', 'rows') in WIRINGS, f"{where}: wiring must be one of {WIRINGS}")
    _need(e.get('start', 'top-left') in STARTS, f"{where}: start must be one of {STARTS}")
    return e


def _mounted_size(panel, entry):
    if entry.get('rotate', 0) in (90, 270):
        return panel.h, panel.w
    return panel.w, panel.h


def validate(data):
    """Raise ConfigError if data is not a usable display config."""
    _need(isinstance(data, dict), "the file must contain a JSON object")
    _need(data.get('protoface_display') == FORMAT_VERSION,
          f"\"protoface_display\" must be {FORMAT_VERSION}")
    _need(isinstance(data.get('name'), str) and 0 < len(data['name']) <= 32,
          "\"name\" must be a string of 1-32 characters")

    panels = data.get('panels')
    _need(isinstance(panels, list) and panels, "\"panels\" must be a non-empty list")
    ids = set()
    total = 0
    for i, p in enumerate(panels):
        where = f"panels[{i}]"
        _need(isinstance(p, dict), f"{where} must be an object")
        pid = p.get('id')
        _need(isinstance(pid, str) and pid, f"{where}: missing \"id\"")
        where = f"panel \"{pid}\""
        _need(pid not in ids, f"{where}: id used twice")
        ids.add(pid)
        for k in ('w', 'h'):
            _need(_is_int(p.get(k)) and 1 <= p[k] <= 256, f"{where}: \"{k}\" must be 1-256")
        for k in ('x', 'y'):
            _need(_is_int(p.get(k)) and p[k] >= 0, f"{where}: \"{k}\" must be 0 or more")
        if 'pixels' in p:
            px = p['pixels']
            _need(isinstance(px, list) and px, f"{where}: \"pixels\" must be a non-empty list")
            seen = set()
            for xy in px:
                _need(isinstance(xy, list) and len(xy) == 2 and all(_is_int(v) for v in xy),
                      f"{where}: each pixel must be [x, y]")
                x, y = xy
                _need(0 <= x < p['w'] and 0 <= y < p['h'],
                      f"{where}: pixel [{x}, {y}] is outside its {p['w']}x{p['h']} area")
                _need((x, y) not in seen, f"{where}: pixel [{x}, {y}] listed twice")
                seen.add((x, y))
            total += len(px)
        else:
            total += p['w'] * p['h']
    _need(total <= MAX_PIXELS, f"{total} pixels in total; the limit is {MAX_PIXELS}")

    m = data.get('mirror')
    if m is not None:
        _need(isinstance(m, dict) and isinstance(m.get('from'), str) and isinstance(m.get('to'), str),
              "\"mirror\" needs \"from\" and \"to\" side names")
        _need(m.get('flip', 'none') in ('none', 'h'), "mirror \"flip\" must be \"none\" or \"h\"")
        sides = {p.get('side') for p in panels}
        for k in ('from', 'to'):
            _need(m[k] in sides, f"mirror \"{k}\": no panel has side \"{m[k]}\"")

    outputs = data.get('outputs')
    _need(isinstance(outputs, list) and outputs, "\"outputs\" must be a non-empty list")
    by_id = {p['id']: p for p in panels}
    used = {}
    for i, o in enumerate(outputs):
        _need(isinstance(o, dict) and o.get('type') in OUTPUT_TYPES,
              f"outputs[{i}]: \"type\" must be one of {OUTPUT_TYPES}")
        where = f"output \"{o.get('name', i)}\""
        key = 'place' if o['type'] == 'hub75' else 'chain'
        entries = o.get(key)
        _need(isinstance(entries, list) and entries, f"{where}: \"{key}\" must be a non-empty list")
        for e in entries:
            e = _entry(e, where)
            pid = e['panel']
            _need(pid in by_id, f"{where}: unknown panel \"{pid}\"")
            _need(pid not in used, f"panel \"{pid}\" is used by {used.get(pid)} and {where}")
            used[pid] = where
            panel = Panel(by_id[pid], 0)
            if o['type'] == 'max7219':
                _need(not panel.custom and _mounted_size(panel, e) == (8, 8),
                      f"{where}: panel \"{pid}\" must be an 8x8 grid for a MAX7219 module")
            if o['type'] == 'hub75':
                for k in ('x', 'y'):
                    _need(_is_int(e.get(k)) and e[k] >= 0, f"{where}: \"{pid}\" needs \"x\" and \"y\"")
                w, h = _mounted_size(panel, e)
                fb_w = o.get('width', 0) * o.get('chain', 1)
                _need(e['x'] + w <= fb_w and e['y'] + h <= o.get('height', 0),
                      f"{where}: panel \"{pid}\" does not fit in the {fb_w}x{o.get('height')} framebuffer")
        if o['type'] == 'ws2812':
            _need(_is_int(o.get('pin')), f"{where}: \"pin\" is required")
            _need(o.get('wiring', 'rows') in WIRINGS, f"{where}: wiring must be one of {WIRINGS}")
            _need(o.get('start', 'top-left') in STARTS, f"{where}: start must be one of {STARTS}")
        if o['type'] == 'hub75':
            for k in ('width', 'height', 'pins'):
                _need(k in o, f"{where}: \"{k}\" is required")
        if o['type'] == 'max7219':
            _need(isinstance(o.get('pins'), dict), f"{where}: \"pins\" is required")
    missing = [p['id'] for p in panels if p['id'] not in used]
    _need(not missing, f"panels not shown by any output: {', '.join(missing)}")


def load(path):
    with open(path, encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"not valid JSON: {e}") from None
    validate(data)
    return DisplayConfig(data, path)


def loads(text):
    data = json.loads(text)
    validate(data)
    return DisplayConfig(data)


def presets():
    """{name: DisplayConfig} for the presets shipped next to this file."""
    out = {}
    for fn in sorted(os.listdir(PRESET_DIR)):
        if fn.endswith('.json') and not fn.endswith('.schema.json'):
            cfg = load(os.path.join(PRESET_DIR, fn))
            out[cfg.name] = cfg
    return out


def _main(paths):
    ok = True
    for path in paths:
        try:
            cfg = load(path)
        except (ConfigError, OSError) as e:
            ok = False
            print(f"FAIL {path}: {e}")
            continue
        w, h = cfg.canvas_size
        kinds = ', '.join(sorted({o['type'] for o in cfg.outputs}))
        print(f"ok   {path}: \"{cfg.name}\", {len(cfg.panels)} panels, {cfg.total_pixels} pixels, "
              f"canvas {w}x{h}, outputs: {kinds}, layout ID {cfg.layout_id():08x}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
