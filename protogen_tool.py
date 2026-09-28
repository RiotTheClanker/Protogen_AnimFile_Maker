#!/usr/bin/env python3
"""
protogen_tool.py  —  Unified Protogen animation tool
Combines:
  • Face Painter  (draw LED animations, save .anim)
  • Simulator     (play back .anim with sound-reaction preview)
  • Export to .h  (convert any frame from a .anim to fallback_anim.h)

Layout modes
  Layout 14  (original)  :  7 panels per side  × 2 = 14 total
                             Left  side (left chain,  GP2): EYE(0,1)  MOUTH[2-5]   NOSE(6)
                             Right side (right chain, GP3): EYE(7,8)  MOUTH[9-12]  NOSE(13)
  Layout 11  (new)       :  Nose side = 6 panels, plain side = 5 panels = 11 total
                             Nose side  (left chain,  GP2): EYE(0,1)  MOUTH[2-4]   NOSE(5)
                             Plain side (right chain, GP3): EYE(6,7)  MOUTH[8-10]

Both sides are drawn in the Painter / Simulator (first side on top, second
side below).  Tick "Mirror to other side" to paint both sides at once, and
"Flip left↔right" if side 2 should be a mirror image of side 1.

The layout is selected in the toolbar and stored in the .anim file header
byte 5 (panel count: 14 or 11).  It must match PROTOGEN_LAYOUT in the
ProtoFace firmware.

Requires: tkinter (stdlib), numpy, pyaudio (optional – for live mic)
  pip install numpy pyaudio
"""

import struct, copy, os, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser, ttk
import numpy as np

try:
    import pyaudio
    PYAUDIO_OK = True
except ImportError:
    PYAUDIO_OK = False

# ── Fixed constants ───────────────────────────────────────────────────────────
MAGIC          = b'ANIM'
VERSION        = 0x01
LEDS_PER_PANEL = 64
LED_BYTES      = 5
FRAME_HDR_SIZE = 4

SOUND_STATIC = 0
SOUND_SNAP   = 1
SOUND_LINEAR = 2
TIMING_TIMED = 0
TIMING_SOUND = 1

# SOUND-timed frames advance when the volume rises through this level
# (after being held for at least duration_ms).  Must match the firmware's
# SOUND_TRIGGER_LEVEL / SOUND_TRIGGER_RELEASE.
SOUND_TRIGGER_LEVEL   = 128
SOUND_TRIGGER_RELEASE = 96

DEFAULT_LAYOUT = 11   # matches the ProtoFace firmware default

# ── Layout descriptors ────────────────────────────────────────────────────────
# Each layout is two "sides".  A side lists its panels by role; the first
# side is wired to the left LED chain, the second to the right LED chain.
# Panels are numbered in chain order: side 0 first, then side 1.

def _make_layout(panels, name, sides):
    left  = sides[0]
    right = sides[1]
    def side_panels(s):
        return s['eyes'] + s['mouth'] + ([s['nose']] if s['nose'] is not None else [])
    return dict(
        name           = name,
        PANELS         = panels,
        TOTAL_LEDS     = panels * LEDS_PER_PANEL,
        SIDES          = sides,
        # Legacy keys (side 0) — kept for code that only cares about one side
        PANEL_EYE_L    = left['eyes'][0],
        PANEL_EYE_R    = left['eyes'][1],
        PANEL_MOUTH    = left['mouth'],
        PANEL_NOSE     = left['nose'],
        PANEL_EYE_L2   = right['eyes'][0],
        PANEL_EYE_R2   = right['eyes'][1],
        PANEL_MOUTH2   = right['mouth'],
        # Chain split used by the firmware
        LEDS_LEFT      = len(side_panels(left))  * LEDS_PER_PANEL,
        LEDS_RIGHT     = len(side_panels(right)) * LEDS_PER_PANEL,
    )

LAYOUTS = {
    11: _make_layout(11, "Layout 11  (6+5, nose side + plain side)", [
        dict(label='NOSE SIDE  (left chain)',   eyes=[0, 1], mouth=[2, 3, 4],     nose=5),
        dict(label='PLAIN SIDE  (right chain)', eyes=[6, 7], mouth=[8, 9, 10],    nose=None),
    ]),
    14: _make_layout(14, "Layout 14  (7+7, original)", [
        dict(label='LEFT SIDE  (left chain)',   eyes=[0, 1], mouth=[2, 3, 4, 5],     nose=6),
        dict(label='RIGHT SIDE  (right chain)', eyes=[7, 8], mouth=[9, 10, 11, 12],  nose=13),
    ]),
}

# Active layout – modules read this dict.  The App overwrites it on change.
_L = LAYOUTS[DEFAULT_LAYOUT]

def set_layout(panel_count):
    global _L
    _L = LAYOUTS[panel_count]

# Convenience accessors (always reflect current layout)
def PANELS():          return _L['PANELS']
def TOTAL_LEDS():      return _L['TOTAL_LEDS']
def PANEL_EYE_L():     return _L['PANEL_EYE_L']
def PANEL_EYE_R():     return _L['PANEL_EYE_R']
def PANEL_MOUTH():     return _L['PANEL_MOUTH']
def PANEL_NOSE():      return _L['PANEL_NOSE']

# ── Shared helpers ────────────────────────────────────────────────────────────
def panel_offset(p):
    return p * LEDS_PER_PANEL

def xy_to_led_idx(panel, x, y):
    return panel_offset(panel) + y * 8 + x

def pack_linear(m, b):
    return (max(0, min(15, int(m))) << 4) | max(0, min(15, int(b)))

def blank_led():
    return [0, 0, 0, SOUND_STATIC, 0]

def blank_led_list(n=None):
    return [blank_led() for _ in range(TOTAL_LEDS() if n is None else n)]

def hex_color(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

def _panel_roles(layout):
    """Map panel → (side_index, role, index_within_role)."""
    roles = {}
    for si, side in enumerate(layout['SIDES']):
        for i, p in enumerate(side['eyes']):
            roles[p] = (si, 'eyes', i)
        for i, p in enumerate(side['mouth']):
            roles[p] = (si, 'mouth', i)
        if side['nose'] is not None:
            roles[side['nose']] = (si, 'nose', 0)
    return roles

def _role_panel(layout, side_idx, role, i):
    side = layout['SIDES'][side_idx]
    if role == 'nose':
        return side['nose']
    panels = side[role]
    return panels[i] if i < len(panels) else None

def mirror_panel_map(layout, flip=False):
    """
    Map each panel to its counterpart on the other side (or None).
    With flip=True the panel order within each region is reversed
    (left eye ↔ right eye, first mouth panel ↔ last), as for a mirror image.
    """
    out = {}
    for p, (si, role, i) in _panel_roles(layout).items():
        other = 1 - si
        if flip and role != 'nose':
            n = len(layout['SIDES'][other][role])
            i = n - 1 - i
            if i < 0:
                out[p] = None
                continue
        out[p] = _role_panel(layout, other, role, i)
    return out

def remap_leds(leds, src_layout, dst_layout):
    """
    Convert one frame's LED list between layouts, matching panels by role
    (eye → eye, mouth panel n → mouth panel n, nose → nose).  Panels with
    no counterpart in the destination are dropped; new ones start blank.
    """
    out = blank_led_list(dst_layout['TOTAL_LEDS'])
    for p, (si, role, i) in _panel_roles(src_layout).items():
        q = _role_panel(dst_layout, si, role, i)
        if q is None:
            continue
        src = panel_offset(p)
        dst = panel_offset(q)
        for k in range(LEDS_PER_PANEL):
            if src + k < len(leds):
                out[dst + k] = list(leds[src + k])
    return out

# ── Binary I/O ────────────────────────────────────────────────────────────────
def _u8(v):
    return max(0, min(255, int(v)))

def make_frame_bytes(duration_ms, timing_mode, leds):
    """Pack one animation frame into bytes."""
    n = TOTAL_LEDS()
    assert len(leds) == n, f"Expected {n} LEDs, got {len(leds)}"
    data = bytearray(struct.pack('<HBB', max(0, min(0xFFFF, int(duration_ms))),
                                 _u8(timing_mode), 0))
    for (r, g, b, sm, p) in leds:
        data += bytes((_u8(r), _u8(g), _u8(b), _u8(sm), _u8(p)))
    return bytes(data)

def write_anim(path, frames_bytes):
    panels = PANELS()
    with open(path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('BBBB', VERSION, panels, 0, 0))
        for frame in frames_bytes:
            f.write(frame)
    print(f"Wrote {len(frames_bytes)} frames → {path}  (layout {panels})")

def load_anim(path):
    """
    Returns (version, panels, frames) where each frame is:
      {'duration_ms': int, 'timing_mode': int, 'leds': list-of-[r,g,b,sm,p]}
    Automatically uses the panel count stored in the file header.
    """
    frames = []
    with open(path, 'rb') as f:
        hdr = f.read(8)
        if len(hdr) < 8 or hdr[:4] != MAGIC:
            raise ValueError("Not a valid .anim file")
        version   = hdr[4]
        panels    = hdr[5]
        if panels not in LAYOUTS:
            raise ValueError(f"Unknown panel count {panels} in file header "
                             f"(supported: {sorted(LAYOUTS.keys())})")
        total_leds = panels * LEDS_PER_PANEL
        frame_size = FRAME_HDR_SIZE + total_leds * LED_BYTES
        while True:
            raw = f.read(frame_size)
            if len(raw) < frame_size:
                break   # ignore a truncated trailing frame (firmware does too)
            duration_ms = struct.unpack_from('<H', raw, 0)[0]
            timing_mode = raw[2]
            leds = [list(raw[o:o + LED_BYTES])
                    for o in range(FRAME_HDR_SIZE, frame_size, LED_BYTES)]
            frames.append({
                'duration_ms': duration_ms,
                'timing_mode': timing_mode,
                'leds': leds,
            })
    return version, panels, frames

# ── Shared canvas layout builder ──────────────────────────────────────────────
SIDE_LABEL_H = 16    # vertical space reserved for each side's title

def fit_cell_size(widget, preferred):
    """Shrink the LED cell size so both sides fit on the screen vertically."""
    try:
        screen_h = widget.winfo_screenheight()
    except tk.TclError:
        return preferred
    # 4 panel rows (2 per side) + gaps/labels + window chrome
    avail = screen_h - 260
    return max(8, min(preferred, avail // 34))

def build_led_rects(cell, gap):
    """
    Build canvas coordinates for every LED in the current layout.

    Each side is drawn as: eyes (2×8×8) top-left, nose (8×8) top-right,
    mouth (n×8×8) underneath.  The first side (left chain) is drawn on top,
    the second side (right chain) below it.
    Returns (rects_dict, canvas_w, canvas_h, side_titles).
    rects_dict maps flat_led_index → (x1, y1, x2, y2).
    side_titles is a list of (x, y, text) for labelling each side.
    """
    rects  = {}
    titles = []
    row_h  = 8 * cell
    max_mouth = max(len(s['mouth']) for s in _L['SIDES'])

    def add_panel(panel, ox, oy):
        for y in range(8):
            for x in range(8):
                flat = xy_to_led_idx(panel, x, y)
                x1 = ox + x * cell
                y1 = oy + y * cell
                rects[flat] = (x1, y1, x1 + cell - 1, y1 + cell - 1)

    top = gap
    for side in _L['SIDES']:
        titles.append((gap, top, side['label']))
        eye_oy   = top + SIDE_LABEL_H + gap
        mouth_ox = cell + gap
        mouth_oy = eye_oy + row_h + gap
        eye_ox   = mouth_ox - cell

        for i, panel in enumerate(side['eyes']):
            add_panel(panel, eye_ox + i * row_h, eye_oy)
        for i, panel in enumerate(side['mouth']):
            add_panel(panel, mouth_ox + i * row_h, mouth_oy)
        if side['nose'] is not None:
            add_panel(side['nose'], mouth_ox + len(side['mouth']) * row_h, eye_oy)

        top = mouth_oy + row_h + gap * 2

    canvas_w = cell + gap + (max_mouth + 1) * row_h + gap
    canvas_h = top
    return rects, canvas_w, canvas_h, titles


def draw_region_outlines(canvas, led_rects, titles=()):
    """Draw colored outlines around eye / mouth / nose regions of both sides."""
    for x, y, text in titles:
        canvas.create_text(x, y, text=text, anchor='nw',
                           fill='#aaaaaa', font=('Helvetica', 9, 'bold'))

    for side in _L['SIDES']:
        regions = [
            (side['eyes'],  '#4fc3f7', 'EYE'),
            (side['mouth'], '#e94560', 'MOUTH'),
        ]
        if side['nose'] is not None:
            regions.append(([side['nose']], '#a5d6a7', 'NOSE'))

        for panels, color, label in regions:
            rects = [led_rects[panel_offset(p) + i]
                     for p in panels for i in range(LEDS_PER_PANEL)
                     if panel_offset(p) + i in led_rects]
            if not rects:
                continue
            x1 = min(r[0] for r in rects) - 3
            y1 = min(r[1] for r in rects) - 3
            x2 = max(r[2] for r in rects) + 3
            y2 = max(r[3] for r in rects) + 3
            canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, fill='')
            canvas.create_text(x1 + 4, y1 - 1, text=label, anchor='sw',
                               fill=color, font=('Helvetica', 8, 'bold'))


# ── Mic monitor ───────────────────────────────────────────────────────────────
class MicMonitor:
    def __init__(self):
        self.volume  = 0
        self.running = False
        self._thread = None
        self._pa     = None
        self._stream = None

    def start(self):
        if not PYAUDIO_OK:
            return False
        try:
            self._pa     = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=1, rate=44100,
                input=True, frames_per_buffer=1024)
            self.running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            print(f"Mic error: {e}")
            self.stop()
            return False

    def stop(self):
        self.running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        self._thread = None
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
        self.volume = 0

    def _run(self):
        while self.running:
            try:
                data = self._stream.read(1024, exception_on_overflow=False)
                arr  = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms  = np.sqrt(np.mean(arr ** 2))
                self.volume = min(255, int(rms / 8000.0 * 255))
            except Exception:
                break

# ── Sound reaction helper ─────────────────────────────────────────────────────
def apply_sound(led, vol):
    r, g, b, sm, param = led
    if sm == SOUND_STATIC:
        return r, g, b
    elif sm == SOUND_SNAP:
        return (r, g, b) if vol >= param else (0, 0, 0)
    elif sm == SOUND_LINEAR:
        m     = (param >> 4) & 0x0F
        bv    = param & 0x0F
        scale = max(0.0, min(1.0, (m * vol / 255.0) + (bv / 15.0)))
        return int(r * scale), int(g * scale), int(b * scale)
    return r, g, b


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 – Painter
# ─────────────────────────────────────────────────────────────────────────────
class PainterTab:
    CELL = 28
    GAP  = 20

    def __init__(self, parent, ensure_layout):
        self.frame = tk.Frame(parent, bg='#1a1a2e')
        self._ensure_layout = ensure_layout

        self.frames     = []
        self.frame_meta = []
        self.current    = 0

        self.draw_color  = (0, 255, 255)
        self.sound_mode  = tk.IntVar(value=SOUND_STATIC)
        self.timing_mode = tk.IntVar(value=TIMING_TIMED)
        self.duration_ms = tk.IntVar(value=500)
        self.snap_thresh = tk.IntVar(value=128)
        self.linear_m    = tk.DoubleVar(value=12.0)
        self.linear_b    = tk.DoubleVar(value=2.0)
        self.eraser      = tk.BooleanVar(value=False)
        self.mirror      = tk.BooleanVar(value=True)
        self.flip        = tk.BooleanVar(value=False)

        self._build_canvas()
        self._build_controls()
        self.new_frame()

    # ── Canvas ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        cell = fit_cell_size(self.frame, self.CELL)
        self._led_rects, cw, ch, self._titles = build_led_rects(cell, self.GAP)
        self._mirror_maps = {False: mirror_panel_map(_L, flip=False),
                             True:  mirror_panel_map(_L, flip=True)}
        self.canvas = tk.Canvas(self.frame, width=cw, height=ch,
                                bg='#0d0d1a', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8, anchor='n')
        self.canvas.bind('<Button-1>', self._on_paint)
        self.canvas.bind('<B1-Motion>', self._on_paint)
        self._canvas_items = {}
        self._item_to_flat = {}

    def rebuild_canvas(self, old_layout):
        """Call after layout change to resize and redraw canvas."""
        self.canvas.destroy()
        self._canvas_items.clear()
        self._build_canvas()
        # Canvas must stay left of the controls panel
        self.canvas.pack_forget()
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8, anchor='n',
                         before=self._ctrl)
        # Carry existing artwork across, matching panels by role
        self.frames = [remap_leds(leds, old_layout, _L) for leds in self.frames]
        if not self.frames:
            self.frames     = [blank_led_list()]
            self.frame_meta = [{'duration_ms': 500, 'timing_mode': TIMING_TIMED}]
            self.current    = 0
        self._build_fill_buttons()
        self._draw_all()

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        ctrl = tk.Frame(self.frame, bg='#1a1a2e', width=230)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        ctrl.pack_propagate(False)
        self._ctrl = ctrl

        def section(t):
            tk.Label(ctrl, text=t, bg='#1a1a2e', fg='#e94560',
                     font=('Helvetica', 9, 'bold')).pack(anchor='w', pady=(10, 0))

        # Brush
        section("BRUSH")
        tk.Button(ctrl, text="Pick Color", command=self._pick_color,
                  bg='#16213e', fg='white').pack(fill=tk.X, pady=2)
        self.color_preview = tk.Label(ctrl, bg=hex_color(self.draw_color), height=2)
        self.color_preview.pack(fill=tk.X, pady=2)
        tk.Checkbutton(ctrl, text="Eraser", variable=self.eraser,
                       bg='#1a1a2e', fg='white', selectcolor='#16213e').pack(anchor='w')
        tk.Checkbutton(ctrl, text="Mirror to other side", variable=self.mirror,
                       bg='#1a1a2e', fg='white', selectcolor='#16213e').pack(anchor='w')
        tk.Checkbutton(ctrl, text="  Flip left↔right when mirroring", variable=self.flip,
                       bg='#1a1a2e', fg='white', selectcolor='#16213e').pack(anchor='w')

        # Sound
        section("SOUND REACTION")
        for lbl, val in [("Static", SOUND_STATIC), ("Snap", SOUND_SNAP),
                          ("Linear y=mx+b", SOUND_LINEAR)]:
            tk.Radiobutton(ctrl, text=lbl, variable=self.sound_mode, value=val,
                           bg='#1a1a2e', fg='white', selectcolor='#16213e').pack(anchor='w')

        tk.Label(ctrl, text="Snap threshold (0-255):", bg='#1a1a2e', fg='#888').pack(anchor='w')
        tk.Scale(ctrl, from_=0, to=255, orient=tk.HORIZONTAL, variable=self.snap_thresh,
                 bg='#1a1a2e', fg='white', troughcolor='#16213e',
                 highlightthickness=0).pack(fill=tk.X)

        tk.Label(ctrl, text="Linear m (slope, 0-15):", bg='#1a1a2e', fg='#888').pack(anchor='w')
        tk.Scale(ctrl, from_=0, to=15, resolution=1, orient=tk.HORIZONTAL,
                 variable=self.linear_m, bg='#1a1a2e', fg='white',
                 troughcolor='#16213e', highlightthickness=0).pack(fill=tk.X)

        tk.Label(ctrl, text="Linear b (offset, 0-15):", bg='#1a1a2e', fg='#888').pack(anchor='w')
        tk.Scale(ctrl, from_=0, to=15, resolution=1, orient=tk.HORIZONTAL,
                 variable=self.linear_b, bg='#1a1a2e', fg='white',
                 troughcolor='#16213e', highlightthickness=0).pack(fill=tk.X)

        # Timing
        section("FRAME TIMING")
        for lbl, val in [("Timed", TIMING_TIMED), ("Sound triggered", TIMING_SOUND)]:
            tk.Radiobutton(ctrl, text=lbl, variable=self.timing_mode, value=val,
                           bg='#1a1a2e', fg='white', selectcolor='#16213e').pack(anchor='w')
        tk.Label(ctrl, text="Duration ms (min hold if sound):",
                 bg='#1a1a2e', fg='#888').pack(anchor='w')
        tk.Scale(ctrl, from_=0, to=5000, resolution=50, orient=tk.HORIZONTAL,
                 variable=self.duration_ms, bg='#1a1a2e', fg='white',
                 troughcolor='#16213e', highlightthickness=0).pack(fill=tk.X)

        # Frames
        section("FRAMES")
        row = tk.Frame(ctrl, bg='#1a1a2e')
        row.pack(fill=tk.X)
        for lbl, cmd in [("+ New", self.new_frame), ("◀", self.prev_frame),
                          ("▶", self.next_frame),   ("Copy", self.copy_frame),
                          ("✕", self.delete_frame)]:
            tk.Button(row, text=lbl, command=cmd,
                      bg='#3d0000' if lbl == '✕' else '#16213e',
                      fg='white', padx=2).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.frame_label = tk.Label(ctrl, text="Frame 1/1", bg='#1a1a2e', fg='white')
        self.frame_label.pack()

        # Fill — built dynamically to match current layout
        section("FILL REGION")
        self._ctrl_fill_frame = tk.Frame(ctrl, bg='#1a1a2e')
        self._ctrl_fill_frame.pack(fill=tk.X)
        self._build_fill_buttons()

        # File
        section("FILE")
        tk.Button(ctrl, text="Open .anim", command=self._open_anim,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        tk.Button(ctrl, text="Save .anim", command=self.save_anim,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        tk.Button(ctrl, text="Clear Frame", command=self.clear_frame,
                  bg='#3d0000', fg='white').pack(fill=tk.X, pady=2)

    def _build_fill_buttons(self):
        for w in self._ctrl_fill_frame.winfo_children():
            w.destroy()
        regions = []
        for si, side in enumerate(_L['SIDES']):
            tag = f"side {si + 1}"
            regions += [
                (f"Eye ({tag})",   side['eyes']),
                (f"Mouth ({tag})", side['mouth']),
            ]
            if side['nose'] is not None:
                regions.append((f"Nose ({tag})", [side['nose']]))
        regions.append(("All", list(range(_L['PANELS']))))
        regions.append(("Copy side 1 → side 2", None))
        for lbl, panels in regions:
            cmd = (self._copy_side_to_other if panels is None
                   else (lambda p=panels: self._fill_panels(p)))
            tk.Button(self._ctrl_fill_frame, text=lbl, command=cmd,
                      bg='#16213e', fg='white').pack(fill=tk.X, pady=1)

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw_all(self):
        self.canvas.delete('all')
        self._canvas_items.clear()
        self._item_to_flat.clear()

        leds = self.frames[self.current]
        for flat, coords in self._led_rects.items():
            r, g, b = leds[flat][0], leds[flat][1], leds[flat][2]
            item = self.canvas.create_rectangle(
                *coords, fill=hex_color((r, g, b)), outline='#1a1a2e', width=1)
            self._canvas_items[flat] = item
            self._item_to_flat[item] = flat

        draw_region_outlines(self.canvas, self._led_rects, self._titles)
        self.frame_label.config(text=f"Frame {self.current+1}/{len(self.frames)}")

    def _update_cell(self, flat):
        if flat not in self._canvas_items:
            return
        r, g, b = self.frames[self.current][flat][:3]
        self.canvas.itemconfig(self._canvas_items[flat], fill=hex_color((r, g, b)))

    # ── Input ─────────────────────────────────────────────────────────────────
    def _current_brush(self):
        if self.eraser.get():
            return blank_led()
        sm = self.sound_mode.get()
        param = (self.snap_thresh.get() if sm == SOUND_SNAP else
                 pack_linear(self.linear_m.get(), self.linear_b.get())
                 if sm == SOUND_LINEAR else 0)
        r, g, b = self.draw_color
        return [r, g, b, sm, param]

    def _mirror_flat(self, flat):
        """
        Matching LED on the other side: same x/y on the matching panel, or,
        with "Flip" ticked, the left-right mirror image (panel order within
        each region reversed and x → 7-x).
        """
        flip = self.flip.get()
        panel, pix = divmod(flat, LEDS_PER_PANEL)
        other = self._mirror_maps[flip].get(panel)
        if other is None:
            return None
        if flip:
            y, x = divmod(pix, 8)
            pix = y * 8 + (7 - x)
        return panel_offset(other) + pix

    def _on_paint(self, event):
        flat = next((self._item_to_flat[i]
                     for i in self.canvas.find_overlapping(event.x, event.y,
                                                           event.x, event.y)
                     if i in self._item_to_flat), None)
        if flat is None:
            return
        leds  = self.frames[self.current]
        brush = self._current_brush()
        targets = [flat]
        if self.mirror.get():
            m = self._mirror_flat(flat)
            if m is not None:
                targets.append(m)
        for t in targets:
            leds[t] = list(brush)
            self._update_cell(t)

    # ── Frame ops ─────────────────────────────────────────────────────────────
    def _save_meta(self):
        if self.frame_meta:
            self.frame_meta[self.current] = {
                'duration_ms': self.duration_ms.get(),
                'timing_mode': self.timing_mode.get(),
            }

    def _load_meta(self):
        m = self.frame_meta[self.current]
        self.duration_ms.set(m.get('duration_ms', 500))
        self.timing_mode.set(m.get('timing_mode', TIMING_TIMED))

    def new_frame(self):
        if self.frames:
            self._save_meta()
        self.frames.append(blank_led_list())
        self.frame_meta.append({'duration_ms': 500, 'timing_mode': TIMING_TIMED})
        self.current = len(self.frames) - 1
        self._load_meta()
        self._draw_all()

    def copy_frame(self):
        self._save_meta()
        self.frames.append(copy.deepcopy(self.frames[self.current]))
        self.frame_meta.append(dict(self.frame_meta[self.current]))
        self.current = len(self.frames) - 1
        self._load_meta()
        self._draw_all()

    def delete_frame(self):
        if len(self.frames) <= 1:
            messagebox.showinfo("Info", "Can't delete the only frame.")
            return
        del self.frames[self.current]
        del self.frame_meta[self.current]
        self.current = max(0, self.current - 1)
        self._load_meta()
        self._draw_all()

    def prev_frame(self):
        if self.current > 0:
            self._save_meta()
            self.current -= 1
            self._load_meta()
            self._draw_all()

    def next_frame(self):
        if self.current < len(self.frames) - 1:
            self._save_meta()
            self.current += 1
            self._load_meta()
            self._draw_all()

    def clear_frame(self):
        self.frames[self.current] = blank_led_list()
        self._draw_all()

    def _fill_panels(self, panels):
        brush = self._current_brush()
        leds  = self.frames[self.current]
        for p in panels:
            base = panel_offset(p)
            for i in range(LEDS_PER_PANEL):
                leds[base + i] = list(brush)
        self._draw_all()

    def _copy_side_to_other(self):
        leds = self.frames[self.current]
        side = _L['SIDES'][0]
        for p in side['eyes'] + side['mouth'] + [side['nose']]:
            if p is None:
                continue
            for i in range(LEDS_PER_PANEL):
                src = panel_offset(p) + i
                dst = self._mirror_flat(src)
                if dst is not None:
                    leds[dst] = list(leds[src])
        self._draw_all()

    # ── Color ─────────────────────────────────────────────────────────────────
    def _pick_color(self):
        result = colorchooser.askcolor(color=hex_color(self.draw_color),
                                       title="Pick brush color")
        if result and result[0]:
            self.draw_color = tuple(int(v) for v in result[0])
            self.color_preview.config(bg=hex_color(self.draw_color))

    # ── File I/O ──────────────────────────────────────────────────────────────
    def _open_anim(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            _, file_panels, loaded = load_anim(path)
            if not loaded:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(file_panels):
                return
            self.frames     = [[list(led) for led in fr['leds']] for fr in loaded]
            self.frame_meta = [{'duration_ms': fr['duration_ms'],
                                 'timing_mode': fr['timing_mode']} for fr in loaded]
            self.current = 0
            self._load_meta()
            self._draw_all()
            messagebox.showinfo("Opened", f"Loaded {len(self.frames)} frames from\n{path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def save_anim(self):
        self._save_meta()
        path = filedialog.asksaveasfilename(
            defaultextension=".anim",
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            binary_frames = [
                make_frame_bytes(self.frame_meta[i]['duration_ms'],
                                 self.frame_meta[i]['timing_mode'],
                                 [tuple(l[:5]) for l in leds])
                for i, leds in enumerate(self.frames)
            ]
            write_anim(path, binary_frames)
        except Exception as e:
            messagebox.showerror("Error", f"Could not save:\n{e}")
            return
        messagebox.showinfo("Saved", f"{len(binary_frames)} frames → {path}")

    def get_frames_data(self):
        """Return frames in the same dict format used by load_anim."""
        self._save_meta()
        return [
            {
                'duration_ms': self.frame_meta[i]['duration_ms'],
                'timing_mode': self.frame_meta[i]['timing_mode'],
                'leds': [list(led) for led in leds],
            }
            for i, leds in enumerate(self.frames)
        ]


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 – Simulator
# ─────────────────────────────────────────────────────────────────────────────
class SimulatorTab:
    CELL = 22
    GAP  = 18

    def __init__(self, parent, get_painter_frames, ensure_layout):
        self.frame = tk.Frame(parent, bg='#0d0d1a')
        self._get_painter_frames = get_painter_frames
        self._ensure_layout = ensure_layout

        self.frames      = []
        self.current     = 0
        self.playing     = False
        self.mic_active  = False
        self.mic         = MicMonitor()
        self.vol_override = tk.IntVar(value=0)
        self._play_after = None
        self._frame_start = 0.0
        self._trigger_armed = True

        self._build_canvas()
        self._build_controls()
        self._draw_blank()

    # ── Canvas ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        cell = fit_cell_size(self.frame, self.CELL)
        self._led_rects, cw, ch, self._titles = build_led_rects(cell, self.GAP)
        self.canvas = tk.Canvas(self.frame, width=cw, height=ch,
                                bg='#0d0d1a', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8, anchor='n')
        self._canvas_items = {}

    def rebuild_canvas(self):
        """Call after layout change."""
        self._stop_play()
        self.frames = []
        self.canvas.destroy()
        self._canvas_items.clear()
        self._build_canvas()
        self.canvas.pack_forget()
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8, anchor='n',
                         before=self._ctrl)
        self._draw_blank()
        self.file_label.config(text="No file loaded")
        self.frame_label.config(text="Frame -/-")
        self.timing_label.config(text="")

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        ctrl = tk.Frame(self.frame, bg='#0d0d1a', width=240)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        ctrl.pack_propagate(False)
        self._ctrl = ctrl

        def section(t):
            tk.Label(ctrl, text=t, bg='#0d0d1a', fg='#e94560',
                     font=('Helvetica', 9, 'bold')).pack(anchor='w', pady=(10, 0))

        # File
        section("FILE")
        tk.Button(ctrl, text="Open .anim", command=self._open_file,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        tk.Button(ctrl, text="Preview Painter Frames", command=self._load_from_painter,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        self.file_label = tk.Label(ctrl, text="No file loaded", bg='#0d0d1a',
                                   fg='#888', wraplength=200)
        self.file_label.pack(anchor='w')

        # Playback
        section("PLAYBACK")
        self.frame_label = tk.Label(ctrl, text="Frame -/-", bg='#0d0d1a', fg='white')
        self.frame_label.pack()
        self.timing_label = tk.Label(ctrl, text="", bg='#0d0d1a', fg='#4fc3f7',
                                     font=('Helvetica', 8))
        self.timing_label.pack()

        nav = tk.Frame(ctrl, bg='#0d0d1a')
        nav.pack(fill=tk.X, pady=4)
        tk.Button(nav, text="◀ Prev", command=self.prev_frame,
                  bg='#16213e', fg='white').pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(nav, text="Next ▶", command=self.next_frame,
                  bg='#16213e', fg='white').pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.play_btn = tk.Button(ctrl, text="▶ Auto Play", command=self._toggle_play,
                                  bg='#1a472a', fg='white')
        self.play_btn.pack(fill=tk.X, pady=2)

        # Audio
        section("AUDIO INPUT")
        mic_row = tk.Frame(ctrl, bg='#0d0d1a')
        mic_row.pack(fill=tk.X)
        self.mic_btn = tk.Button(mic_row, text="🎤 Use Mic",
                                 command=self._toggle_mic,
                                 bg='#16213e', fg='white')
        self.mic_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        if not PYAUDIO_OK:
            tk.Label(mic_row, text="(pyaudio not installed)",
                     bg='#0d0d1a', fg='#e94560', font=('Helvetica', 7)).pack()

        tk.Label(ctrl, text="Manual volume (0-255):",
                 bg='#0d0d1a', fg='#888').pack(anchor='w', pady=(6, 0))
        self.vol_slider = tk.Scale(ctrl, from_=0, to=255, orient=tk.HORIZONTAL,
                                   variable=self.vol_override, command=self._on_slider,
                                   bg='#0d0d1a', fg='white', troughcolor='#16213e',
                                   highlightthickness=0)
        self.vol_slider.pack(fill=tk.X)

        self.vu_canvas = tk.Canvas(ctrl, height=16, bg='#0d0d1a', highlightthickness=0)
        self.vu_canvas.pack(fill=tk.X, pady=4)
        self.vu_bar = self.vu_canvas.create_rectangle(0, 2, 0, 14,
                                                       fill='#00e676', outline='')

        # Legend
        section("LEGEND")
        for color, label in [('#4fc3f7', 'Eye'), ('#e94560', 'Mouth'), ('#a5d6a7', 'Nose')]:
            row = tk.Frame(ctrl, bg='#0d0d1a')
            row.pack(anchor='w')
            tk.Label(row, bg=color, width=2).pack(side=tk.LEFT, padx=4)
            tk.Label(row, text=label, bg='#0d0d1a', fg='white').pack(side=tk.LEFT)

        tk.Label(ctrl,
                 text="SNAP   = flashes on threshold\n"
                      "LINEAR = y=mx+b brightness\n"
                      f"SOUND frames advance when volume\n"
                      f"rises past {SOUND_TRIGGER_LEVEL}",
                 bg='#0d0d1a', fg='#666', font=('Helvetica', 8),
                 justify=tk.LEFT).pack(anchor='w', pady=8)

    # ── File loading ──────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            _, file_panels, frames = load_anim(path)
            if not frames:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(file_panels):
                return
            self._load_frames(frames)
            self.file_label.config(text=os.path.basename(path))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _load_from_painter(self):
        frames = self._get_painter_frames()
        if not frames:
            messagebox.showinfo("Info", "No frames in Painter yet.")
            return
        self._load_frames(frames)
        self.file_label.config(text="[Painter frames]")

    def _load_frames(self, frames):
        self._stop_play()
        self.frames  = frames
        self.current = 0
        self._render_frame()

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _draw_blank(self):
        self.canvas.delete('all')
        self._canvas_items.clear()
        for flat, coords in self._led_rects.items():
            item = self.canvas.create_rectangle(*coords, fill='#111122',
                                                outline='#1a1a2e', width=1)
            self._canvas_items[flat] = item
        draw_region_outlines(self.canvas, self._led_rects, self._titles)

    def _render_frame(self):
        if not self.frames:
            return
        frame = self.frames[self.current]
        vol   = self._get_volume()

        vu_w  = int(self.vu_canvas.winfo_width() * vol / 255)
        color = '#00e676' if vol < 180 else '#ff5252'
        self.vu_canvas.coords(self.vu_bar, 0, 2, vu_w, 14)
        self.vu_canvas.itemconfig(self.vu_bar, fill=color)

        if frame['timing_mode'] == TIMING_TIMED:
            timing = f"TIMED  |  {frame['duration_ms']}ms"
        else:
            timing = f"SOUND TRIGGERED  |  hold ≥{frame['duration_ms']}ms"
        self.frame_label.config(text=f"Frame {self.current+1}/{len(self.frames)}")
        self.timing_label.config(text=f"{timing}  |  vol={vol}")

        leds = frame['leds']
        for flat, item in self._canvas_items.items():
            if flat >= len(leds):
                continue
            r, g, b = apply_sound(leds[flat], vol)
            self.canvas.itemconfig(item, fill=hex_color((r, g, b)))

    # ── Volume ────────────────────────────────────────────────────────────────
    def _get_volume(self):
        if self.mic_active and self.mic.running:
            return self.mic.volume
        return self.vol_override.get()

    def _on_slider(self, _=None):
        if not self.mic_active:
            self._render_frame()

    # ── Mic ───────────────────────────────────────────────────────────────────
    def _toggle_mic(self):
        if not PYAUDIO_OK:
            messagebox.showerror("Error",
                "pyaudio not installed.\nRun: pip install pyaudio numpy")
            return
        if self.mic_active:
            self.mic.stop()
            self.mic_active = False
            self.mic_btn.config(text="🎤 Use Mic", bg='#16213e')
        else:
            if self.mic.start():
                self.mic_active = True
                self.mic_btn.config(text="🎤 Mic ON", bg='#1a472a')
            else:
                messagebox.showerror("Error", "Could not open microphone.")

    # ── Playback ──────────────────────────────────────────────────────────────
    def _toggle_play(self):
        if self.playing:
            self._stop_play()
        else:
            self._start_play()

    def _start_play(self):
        if not self.frames:
            return
        self.playing = True
        self.play_btn.config(text="⏹ Stop", bg='#6d1a1a')
        self._enter_frame()
        self._play_tick()

    def _stop_play(self):
        self.playing = False
        self.play_btn.config(text="▶ Auto Play", bg='#1a472a')
        if self._play_after:
            self.frame.after_cancel(self._play_after)
            self._play_after = None

    def _enter_frame(self):
        self._frame_start = time.monotonic()
        # Require the volume to drop below the release level before a
        # SOUND frame can trigger, so one loud noise advances only one frame.
        self._trigger_armed = self._get_volume() < SOUND_TRIGGER_RELEASE

    def _play_tick(self):
        """Mirrors the firmware's loop(): re-render, then maybe advance."""
        self._play_after = None
        if not self.playing or not self.frames:
            return
        self._render_frame()
        frame   = self.frames[self.current]
        elapsed = (time.monotonic() - self._frame_start) * 1000.0
        if frame['timing_mode'] == TIMING_TIMED:
            advance = elapsed >= frame['duration_ms']
        else:
            vol = self._get_volume()
            if vol < SOUND_TRIGGER_RELEASE:
                self._trigger_armed = True
            advance = (self._trigger_armed and vol >= SOUND_TRIGGER_LEVEL
                       and elapsed >= frame['duration_ms'])
        if advance:
            self.current = (self.current + 1) % len(self.frames)
            self._enter_frame()
        self._play_after = self.frame.after(16, self._play_tick)

    def next_frame(self):
        if not self.frames:
            return
        self._stop_play()
        self.current = (self.current + 1) % len(self.frames)
        self._render_frame()

    def prev_frame(self):
        if not self.frames:
            return
        self._stop_play()
        self.current = (self.current - 1) % len(self.frames)
        self._render_frame()

    def mic_refresh(self):
        if self.mic_active and not self.playing:
            self._render_frame()

    def on_close(self):
        self._stop_play()
        self.mic.stop()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 – Export to .h
# ─────────────────────────────────────────────────────────────────────────────
class ExportTab:
    def __init__(self, parent, get_painter_frames, ensure_layout):
        self.frame = tk.Frame(parent, bg='#1a1a2e')
        self._get_painter_frames = get_painter_frames
        self._ensure_layout = ensure_layout

        self._anim_frames = []
        self._source_name = ""
        self._chosen_idx  = tk.IntVar(value=1)

        self._build_ui()

    def _build_ui(self):
        f = self.frame
        BG, FG, ACC = '#1a1a2e', 'white', '#e94560'

        def section(t):
            tk.Label(f, text=t, bg=BG, fg=ACC,
                     font=('Helvetica', 10, 'bold')).pack(anchor='w', padx=12, pady=(14, 2))

        # Source
        section("SOURCE")
        src_row = tk.Frame(f, bg=BG)
        src_row.pack(fill=tk.X, padx=12)
        tk.Button(src_row, text="Open .anim file…", command=self._open_file,
                  bg='#0f3460', fg=FG).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(src_row, text="Use Painter frames", command=self._load_from_painter,
                  bg='#0f3460', fg=FG).pack(side=tk.LEFT)

        self.src_label = tk.Label(f, text="No source loaded.", bg=BG, fg='#888')
        self.src_label.pack(anchor='w', padx=12)

        # Frame selector
        section("SELECT FRAME")
        sel_row = tk.Frame(f, bg=BG)
        sel_row.pack(fill=tk.X, padx=12)
        tk.Label(sel_row, text="Frame index (1-based):", bg=BG, fg=FG).pack(side=tk.LEFT)
        self.frame_spin = tk.Spinbox(sel_row, from_=1, to=1,
                                     textvariable=self._chosen_idx,
                                     width=6, bg='#16213e', fg=FG,
                                     command=self._update_preview)
        self.frame_spin.pack(side=tk.LEFT, padx=8)
        tk.Button(sel_row, text="Preview", command=self._update_preview,
                  bg='#16213e', fg=FG).pack(side=tk.LEFT)

        # Preview
        section("FRAME PREVIEW")
        self.preview_text = tk.Text(f, height=12, bg='#0d0d1a', fg='#ccc',
                                    font=('Courier', 9), state=tk.DISABLED,
                                    relief=tk.FLAT, padx=8, pady=6)
        self.preview_text.pack(fill=tk.X, padx=12)

        # Output path
        section("OUTPUT PATH")
        out_row = tk.Frame(f, bg=BG)
        out_row.pack(fill=tk.X, padx=12)
        self.out_var = tk.StringVar(value="fallback_anim.h")
        tk.Entry(out_row, textvariable=self.out_var, bg='#16213e', fg=FG,
                 insertbackground=FG, relief=tk.FLAT).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(out_row, text="Browse…", command=self._browse_out,
                  bg='#16213e', fg=FG).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(f, text="Place the file in ProtoFace/src/ and rebuild the firmware.",
                 bg=BG, fg='#888').pack(anchor='w', padx=12, pady=(2, 0))

        # Export
        tk.Button(f, text="Export fallback_anim.h", command=self._export,
                  bg='#0f3460', fg=FG,
                  font=('Helvetica', 10, 'bold')).pack(pady=14, padx=12, fill=tk.X)

        self.status_label = tk.Label(f, text="", bg=BG, fg='#a5d6a7',
                                     font=('Helvetica', 9))
        self.status_label.pack(anchor='w', padx=12)

    def reset(self):
        """Call after layout change — loaded frames no longer match."""
        self._anim_frames = []
        self._source_name = ""
        self.src_label.config(text="No source loaded.")
        self.frame_spin.config(to=1)
        self._chosen_idx.set(1)
        self.status_label.config(text="")
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.config(state=tk.DISABLED)

    # ── Load source ───────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            _, file_panels, frames = load_anim(path)
            if not frames:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(file_panels):
                return
            self._anim_frames = frames
            self._source_name = os.path.basename(path)
            self._on_frames_loaded()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _load_from_painter(self):
        frames = self._get_painter_frames()
        if not frames:
            messagebox.showinfo("Info", "No frames in Painter yet.")
            return
        self._anim_frames = frames
        self._source_name = "[Painter]"
        self._on_frames_loaded()

    def _on_frames_loaded(self):
        n = len(self._anim_frames)
        self.src_label.config(text=f"{self._source_name}  —  {n} frame(s)")
        self.frame_spin.config(to=n)
        self._chosen_idx.set(1)
        self._update_preview()

    def _selected_index(self):
        return max(1, min(len(self._anim_frames), int(self._chosen_idx.get()))) - 1

    # ── Preview ───────────────────────────────────────────────────────────────
    def _update_preview(self):
        if not self._anim_frames:
            return
        try:
            idx = self._selected_index()
        except (ValueError, tk.TclError):
            return
        frame = self._anim_frames[idx]
        dur   = frame['duration_ms']
        tm    = frame['timing_mode']
        leds  = frame['leds']
        L     = _L

        counts = {0: 0, 1: 0, 2: 0}
        for led in leds:
            sm = led[3]
            if sm in counts:
                counts[sm] += 1

        timing_str = (f"SOUND_TRIGGERED (hold ≥{dur} ms)" if tm == TIMING_SOUND
                      else f"TIMED ({dur} ms)")
        lines = [
            f"Layout     : {L['PANELS']}-panel  ({L['name']})",
            f"Frame      : {idx + 1} of {len(self._anim_frames)}",
            f"Timing     : {timing_str}",
            f"LEDs       : {counts[0]} STATIC  |  {counts[1]} SNAP  |  {counts[2]} LINEAR",
            "",
        ]

        # Lit-LED count per region, so an empty side is obvious before export
        for si, side in enumerate(L['SIDES']):
            regions = [('Eye', side['eyes']), ('Mouth', side['mouth'])]
            if side['nose'] is not None:
                regions.append(('Nose', [side['nose']]))
            parts = []
            for name, panels in regions:
                lit = sum(1 for p in panels for i in range(LEDS_PER_PANEL)
                          if any(leds[panel_offset(p) + i][:3]))
                parts.append(f"{name} {lit}/{len(panels) * LEDS_PER_PANEL}")
            lines.append(f"  Side {si + 1} lit : " + "  ".join(parts))

        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.insert(tk.END, '\n'.join(lines))
        self.preview_text.config(state=tk.DISABLED)

    # ── Browse output ─────────────────────────────────────────────────────────
    def _browse_out(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".h",
            initialfile="fallback_anim.h",
            filetypes=[("C header files", "*.h"), ("All", "*.*")])
        if path:
            self.out_var.set(path)

    # ── Export ────────────────────────────────────────────────────────────────
    def _export(self):
        if not self._anim_frames:
            messagebox.showerror("Error", "No frames loaded.")
            return
        try:
            idx = self._selected_index()
        except (ValueError, tk.TclError):
            messagebox.showerror("Error", "Invalid frame index.")
            return

        out_path = self.out_var.get().strip()
        if not out_path:
            messagebox.showerror("Error", "Please specify an output path.")
            return

        frame  = self._anim_frames[idx]
        total  = len(self._anim_frames)
        if len(frame['leds']) != TOTAL_LEDS():
            messagebox.showerror(
                "Error",
                f"Frame has {len(frame['leds'])} LEDs but the {PANELS()}-panel "
                f"layout needs {TOTAL_LEDS()}. Reload the source.")
            return
        try:
            self._generate_h(frame, idx, total, self._source_name, out_path)
        except OSError as e:
            messagebox.showerror("Error", f"Could not write file:\n{e}")
            return
        self.status_label.config(
            text=f"✓  Written → {out_path}   (frame {idx+1}/{total}, "
                 f"layout {PANELS()})")

    def _generate_h(self, frame, frame_idx, total_frames, source_name, out_path):
        L      = _L
        panels = L['PANELS']
        total  = L['TOTAL_LEDS']
        dur    = frame['duration_ms']
        timing = frame['timing_mode']
        leds   = frame['leds']
        timing_str = "SOUND_TRIGGERED" if timing == TIMING_SOUND else f"TIMED ({dur}ms)"

        # Chain sizes for the comment header
        chain_l = L['LEDS_LEFT']
        chain_r = L['LEDS_RIGHT']

        lines = [
            '#pragma once',
            '#include <stdint.h>',
            '#include <string.h>',
            '',
            '// ── Fallback animation ─────────────────────────────────────────────────────',
            f'// Source    : {source_name}',
            f'// Frame     : {frame_idx + 1} of {total_frames}',
            f'// Timing    : {timing_str}',
            f'// Layout    : {panels}-panel  (left chain={chain_l} LEDs, right chain={chain_r} LEDs)',
            '//',
            '// sound_mode : 0=STATIC  1=SNAP  2=LINEAR',
            '// param      : SNAP   → threshold 0-255',
            '//              LINEAR → high nibble=m (0-15)  low nibble=b (0-15)',
            '//',
            '// The firmware checks FALLBACK_LAYOUT against its PROTOGEN_LAYOUT and',
            '// refuses to build if they differ.',
            '',
            f'#define FALLBACK_LAYOUT {panels}',
            '',
            'struct LEDEntry {',
            '    uint8_t r, g, b;',
            '    uint8_t sound_mode;',
            '    uint8_t param;',
            '};',
            '',
            'struct AnimFrame {',
            '    uint16_t duration_ms;',
            '    uint8_t  timing_mode;',
            f'    LEDEntry leds[{total}];',
            '};',
            '',
            f'static const uint16_t FALLBACK_DURATION_MS = {dur};',
            f'static const uint8_t  FALLBACK_TIMING_MODE = {timing};',
            '',
            f'static const LEDEntry FALLBACK_LEDS[{total}] = {{',
        ]

        for i, led in enumerate(leds):
            r, g, b, sm, p = led[:5]
            comma = ',' if i < total - 1 else ' '
            lines.append(f'    {{{r:3},{g:3},{b:3},{sm},{p}}}{comma}')

        lines += [
            '};',
            '',
            '// ── Helper to copy into an AnimFrame struct if needed ──────────────────────',
            'static inline AnimFrame makeFallbackFrame() {',
            '    AnimFrame f;',
            '    f.duration_ms = FALLBACK_DURATION_MS;',
            '    f.timing_mode = FALLBACK_TIMING_MODE;',
            '    memcpy(f.leds, FALLBACK_LEDS, sizeof(FALLBACK_LEDS));',
            '    return f;',
            '}',
        ]

        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Protogen Tool")
        root.configure(bg='#0d0d1a')

        # ── Layout selector toolbar ──────────────────────────────────────────
        toolbar = tk.Frame(root, bg='#0d0d1a', pady=4)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(toolbar, text="Layout:", bg='#0d0d1a', fg='#aaa',
                 font=('Helvetica', 9, 'bold')).pack(side=tk.LEFT, padx=(10, 4))

        self._layout_var = tk.StringVar(value=str(_L['PANELS']))
        for key, layout in sorted(LAYOUTS.items()):
            tk.Radiobutton(
                toolbar, text=layout['name'],
                variable=self._layout_var, value=str(key),
                bg='#0d0d1a', fg='white', selectcolor='#16213e',
                activebackground='#0d0d1a', activeforeground='white',
                command=self._on_layout_change,
            ).pack(side=tk.LEFT, padx=6)

        self._layout_info = tk.Label(toolbar, text=self._layout_info_text(),
                                     bg='#0d0d1a', fg='#888', font=('Helvetica', 8))
        self._layout_info.pack(side=tk.LEFT, padx=12)

        # ── Notebook ─────────────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use('default')
        style.configure('TNotebook',           background='#0d0d1a', borderwidth=0)
        style.configure('TNotebook.Tab',       background='#16213e', foreground='white',
                                               padding=[12, 6])
        style.map('TNotebook.Tab',
                  background=[('selected', '#0f3460')],
                  foreground=[('selected', 'white')])

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True)

        self.painter  = PainterTab(nb, self.ensure_layout)
        self.sim      = SimulatorTab(nb, self.painter.get_frames_data, self.ensure_layout)
        self.exporter = ExportTab(nb, self.painter.get_frames_data, self.ensure_layout)

        nb.add(self.painter.frame,  text="  🎨  Painter  ")
        nb.add(self.sim.frame,      text="  ▶   Simulator  ")
        nb.add(self.exporter.frame, text="  📄  Export .h  ")

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._mic_refresh()

    def _layout_info_text(self):
        L = _L
        return (f"PANELS={L['PANELS']}  TOTAL_LEDS={L['TOTAL_LEDS']}  "
                f"left_chain={L['LEDS_LEFT']}  right_chain={L['LEDS_RIGHT']}  "
                f"(firmware: PROTOGEN_LAYOUT {L['PANELS']})")

    def _on_layout_change(self):
        new_panels = int(self._layout_var.get())
        if new_panels == _L['PANELS']:
            return
        if not messagebox.askyesno(
                "Switch layout?",
                f"Switch to {new_panels}-panel layout?\n\n"
                "Painter frames are converted panel-by-panel (eyes → eyes, "
                "mouth → mouth, nose → nose); panels that don't exist in the "
                "new layout are dropped.\nContinue?"):
            # Revert radio button
            self._layout_var.set(str(_L['PANELS']))
            return
        self._apply_layout(new_panels)

    def _apply_layout(self, new_panels):
        old = _L
        set_layout(new_panels)
        self._layout_var.set(str(new_panels))
        self._layout_info.config(text=self._layout_info_text())
        # Rebuild canvases and reset per-layout state
        self.painter.rebuild_canvas(old)
        self.sim.rebuild_canvas()
        self.exporter.reset()

    def ensure_layout(self, panels):
        """Offer to switch to a file's layout.  Returns True if it now matches."""
        if panels == _L['PANELS']:
            return True
        if not messagebox.askyesno(
                "Layout mismatch",
                f"This file uses the {panels}-panel layout but the tool is set "
                f"to {_L['PANELS']}-panel.\n\nSwitch to the {panels}-panel layout?"):
            return False
        self._apply_layout(panels)
        return True

    def _mic_refresh(self):
        self.sim.mic_refresh()
        self.root.after(33, self._mic_refresh)

    def _on_close(self):
        self.sim.on_close()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
