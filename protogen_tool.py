#!/usr/bin/env python3
"""
protogen_tool.py  —  Unified Protogen animation tool
Combines:
  • Face Painter  (draw LED animations, save .anim)
  • Simulator     (play back .anim with sound-reaction preview)
  • Export to .h  (convert any frame from a .anim to fallback_anim.h)

Displays
  The face layout comes from a display config (display.json, documented in
  ProtoFace docs/display-config.md).  The Mk2 and Mk3 presets ship in
  displays/; "Open display.json…" in the toolbar loads any other.
  The display must match the one the ProtoFace firmware is set up for.

Both sides are drawn in the Painter / Simulator.  Tick "Mirror to other
side" to paint both sides at once, and "Flip left↔right" if side 2 should be
a mirror image of side 1.

Requires: tkinter (stdlib), numpy, pyaudio (optional – for live mic)
  pip install numpy pyaudio
"""

import copy, os, sys, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser, ttk
import numpy as np

import display_config as dcfg
from anim_io import (read_anim, write_anim, remap_leds, blank_led, blank_leds,
                     apply_sound, pack_linear,
                     SOUND_STATIC, SOUND_SNAP, SOUND_LINEAR, TIMING_TIMED, TIMING_SOUND,
                     SOUND_TRIGGER_LEVEL, SOUND_TRIGGER_RELEASE)
import help_ui
from help_ui import tip
from help_demos import mode_text

try:
    import pyaudio
    PYAUDIO_OK = True
except ImportError:
    PYAUDIO_OK = False

# ── Fixed constants ───────────────────────────────────────────────────────────
DEFAULT_DISPLAY = 'Mk2'   # matches the ProtoFace firmware default

# ── Display configs ───────────────────────────────────────────────────────────
# Every display the tool knows about this session: the shipped presets plus
# any display.json the user opened.  _D is the active one.
DISPLAYS = dcfg.presets()
_D = DISPLAYS[DEFAULT_DISPLAY]

def active():
    return _D

def set_display(cfg):
    global _D
    DISPLAYS[cfg.name] = cfg
    _D = cfg

def TOTAL_LEDS():
    return _D.total_pixels

# ── Shared helpers ────────────────────────────────────────────────────────────
def blank_led_list(n=None):
    return blank_leds(TOTAL_LEDS() if n is None else n)

def hex_color(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

def region_groups(cfg):
    """[(label, side, region, [panels])] in panel order, for outlines and fill buttons."""
    groups, seen = [], {}
    for p in cfg.panels:
        key = (p.side, p.region if p.region is not None else p.id)
        if key not in seen:
            if p.region is None:          # a plain panel: named by its id
                label = p.id
            else:
                label = f"{p.side.upper()} {p.region.upper()}" if p.side else p.region.upper()
            seen[key] = len(groups)
            groups.append((label, p.side, p.region, []))
        groups[seen[key]][3].append(p)
    return groups

REGION_COLORS = {'eye': '#4fc3f7', 'mouth': '#e94560', 'nose': '#a5d6a7'}
EXTRA_COLORS  = ['#ffb74d', '#ce93d8', '#fff176', '#80cbc4', '#f48fb1']

def region_color(region, i):
    return REGION_COLORS.get(region, EXTRA_COLORS[i % len(EXTRA_COLORS)])

def describe_pixel(flat):
    """'LEFT EYE · panel L_EYE · x 3, y 5' for the LED at flat index `flat`."""
    for p in _D.panels:
        if p.offset <= flat < p.offset + p.count:
            x, y = p.pixels[flat - p.offset]
            if p.region is None:          # a plain panel: its id is its name
                return f"panel {p.id}  ·  x {x}, y {y}"
            where = (f"{p.side.upper()} " if p.side else '') + p.region.upper()
            return f"{where}  ·  panel {p.id}  ·  x {x}, y {y}"
    return ''

def describe_led(led):
    """'#00ffff · Snap: on when the volume is 128 or more', or 'off'."""
    if not any(led[:3]):
        return "off (black)"
    return f"{hex_color(led[:3])}  ·  {mode_text(led)}"

def frame_is_lit(leds):
    return any(led[0] or led[1] or led[2] for led in leds)


# ── Example animation ─────────────────────────────────────────────────────────
def _example_regions(cfg):
    """[(kind, x0, y0, x1, y1)] boxes in display pixels to draw the example in."""
    boxes = {}
    for p in cfg.panels:
        if p.region in ('eye', 'mouth', 'nose'):
            key = (p.side, p.region)
            x0, y0, x1, y1 = boxes.get(key, (p.x, p.y, p.x + p.w, p.y + p.h))
            boxes[key] = (min(x0, p.x), min(y0, p.y), max(x1, p.x + p.w), max(y1, p.y + p.h))
    if any(r == 'eye' for _s, r in boxes) and any(r == 'mouth' for _s, r in boxes):
        return [(r, *box) for (_s, r), box in boxes.items()]
    # No eye / mouth regions (one big panel, say): draw a face across the display
    out = []
    sides = sorted({p.side for p in cfg.panels if p.side}) or [None]
    for side in sides:
        ps = [p for p in cfg.panels if p.side == side] if side else list(cfg.panels)
        x0, y0 = min(p.x for p in ps), min(p.y for p in ps)
        x1, y1 = max(p.x + p.w for p in ps), max(p.y + p.h for p in ps)
        w, h = x1 - x0, y1 - y0
        eyes = [(0.15, 0.4)] if len(sides) > 1 else [(0.1, 0.4), (0.6, 0.9)]
        for a, b in eyes:
            out.append(('eye', x0 + a * w, y0 + 0.08 * h, x0 + b * w, y0 + 0.5 * h))
        out.append(('mouth', x0 + 0.1 * w, y0 + 0.6 * h, x0 + 0.9 * w, y0 + 0.95 * h))
    return out


def make_example(cfg):
    """A two-frame example for any display: the eyes blink (Timed frames),
    the mouth opens when you talk (Snap) and the nose glows with your voice
    (Linear).  Returns (frames, frame_meta)."""
    cyan = (0, 255, 255)
    static = [*cyan, SOUND_STATIC, 0]
    snap = [*cyan, SOUND_SNAP, 90]
    glow = [255, 120, 200, SOUND_LINEAR, pack_linear(3, 2)]
    regions = _example_regions(cfg)

    def led_for(kind, u, v, eyes_open):
        if kind == 'eye':
            if eyes_open:
                return static if ((u - 0.5) / 0.45) ** 2 + ((v - 0.5) / 0.42) ** 2 <= 1 else None
            return static if abs(v - 0.6) < 0.1 and abs(u - 0.5) < 0.45 else None
        if kind == 'mouth':
            if abs(v - 0.5) < 0.09 and 0.03 < u < 0.97:
                return static                     # closed mouth line
            open_h = 0.42 * (1 - (2 * u - 1) ** 2)
            return snap if abs(v - 0.5) < open_h else None   # opens when loud
        if kind == 'nose':
            return glow if abs(u - 0.5) < 0.35 * (1 - v) + 0.05 and 0.2 < v < 0.8 else None
        return None

    frames = []
    for eyes_open in (True, False):
        leds = blank_leds(cfg.total_pixels)
        for p in cfg.panels:
            for i, (px, py) in enumerate(p.pixels):
                X, Y = p.x + px + 0.5, p.y + py + 0.5
                for kind, x0, y0, x1, y1 in regions:
                    if x0 <= X < x1 and y0 <= Y < y1:
                        led = led_for(kind, (X - x0) / (x1 - x0), (Y - y0) / (y1 - y0),
                                      eyes_open)
                        if led is not None:
                            leds[p.offset + i] = list(led)
                        break
        frames.append(leds)
    meta = [{'duration_ms': 2500, 'timing_mode': TIMING_TIMED},
            {'duration_ms': 150, 'timing_mode': TIMING_TIMED}]
    return frames, meta

# Quick colour swatches in the Painter (keys 1-8)
SWATCHES = [(255, 255, 255), (255, 0, 0), (255, 128, 0), (255, 255, 0),
            (0, 255, 0), (0, 255, 255), (0, 64, 255), (255, 0, 255)]

UNDO_LIMIT = 100

# ── Shared canvas layout ──────────────────────────────────────────────────────
LABEL_H  = 14        # space above each row of panels for region labels
MIN_CELL = 2         # smallest LED square the canvases shrink to
MAX_CELL = 40        # largest LED square the canvases grow to

def gap_for_cell(cell, max_gap):
    """Spacing between panel rows, shrunk along with small LED cells."""
    return min(max_gap, max(8, cell))

def geometry(cell, max_gap):
    return dcfg.CanvasGeometry(_D, cell, gap_for_cell(cell, max_gap),
                               LABEL_H if _D.outlines else 0)

def fit_cell_to(width, height, max_gap):
    """Largest LED cell size whose layout fits in width × height pixels."""
    for cell in range(MAX_CELL, MIN_CELL, -1):
        g = geometry(cell, max_gap)
        if g.width <= width and g.height <= height:
            return cell
    return MIN_CELL

def draw_region_outlines(canvas, led_rects):
    """Outline and label each region (eye, mouth, nose, …) on each side."""
    if not _D.outlines:
        return
    for i, (label, _side, region, panels) in enumerate(region_groups(_D)):
        rects = [led_rects[p.offset + k] for p in panels for k in range(p.count)
                 if p.offset + k in led_rects]
        if not rects:
            continue
        color = region_color(region, i)
        x1 = min(r[0] for r in rects) - 3
        y1 = min(r[1] for r in rects) - 3
        x2 = max(r[2] for r in rects) + 3
        y2 = max(r[3] for r in rects) + 3
        canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, fill='')
        canvas.create_text(x1 + 4, y1 - 1, text=label, anchor='sw',
                           fill=color, font=('Helvetica', 8, 'bold'))


# ── Window fitting helpers ────────────────────────────────────────────────────
class ScrollFrame(tk.Frame):
    """
    A frame with a vertical scrollbar.  Pack children into `.inner`; when they
    are taller than the window, scroll with the scrollbar or the mouse wheel.
    """
    def __init__(self, parent, bg, width=None):
        super().__init__(parent, bg=bg)
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0,
                                 width=width or 10)
        self._bar = tk.Scrollbar(self, orient=tk.VERTICAL,
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._bar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self._canvas, bg=bg)
        self._win = self._canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', self._on_inner_resize)
        self._canvas.bind('<Configure>', self._on_canvas_resize)
        # Wheel scrolling only while the pointer is over this column
        self.bind('<Enter>', lambda e: self._bind_wheel(True))
        self.bind('<Leave>', lambda e: self._bind_wheel(False))

    def _on_inner_resize(self, _event):
        self._canvas.configure(scrollregion=self._canvas.bbox('all'))

    def _on_canvas_resize(self, event):
        # Children fill the visible width
        self._canvas.itemconfig(self._win, width=event.width)

    def _bind_wheel(self, on):
        if on:
            self.bind_all('<MouseWheel>', self._on_wheel)     # Windows / macOS
            self.bind_all('<Button-4>', self._on_wheel)       # Linux
            self.bind_all('<Button-5>', self._on_wheel)
        else:
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.unbind_all(seq)

    def _on_wheel(self, event):
        if self.inner.winfo_reqheight() <= self._canvas.winfo_height():
            return   # everything already fits
        if getattr(event, 'num', None) == 4:
            step = -1
        elif getattr(event, 'num', None) == 5:
            step = 1
        elif abs(event.delta) >= 120:
            step = -int(event.delta / 120)               # Windows
        else:
            step = -1 if event.delta > 0 else 1          # macOS
        self._canvas.yview_scroll(step, 'units')


class FittedLedCanvas:
    """
    LED canvas that rescales the face to fill whatever space the window gives
    it, so the tool works on small and scaled (high-DPI) screens.  Calls
    `redraw()` after the LED rectangles change size.
    """
    def __init__(self, parent, gap, redraw):
        self.gap     = gap
        self._redraw = redraw
        self._after  = None
        self.cell    = None
        self.canvas  = tk.Canvas(parent, width=320, height=240,
                                 bg='#0d0d1a', highlightthickness=0)
        self.canvas.bind('<Configure>', self._on_resize)
        self.relayout(MIN_CELL * 4)

    def relayout(self, cell=None):
        """Rebuild LED rectangles (also after a layout change)."""
        if cell is None:
            cell = self.cell or MIN_CELL * 4
        self.cell = cell
        self.rects = geometry(cell, self.gap).rects()
        # Tiny cells (big HUB75 faces) lose their colour to a 1px outline
        self.outline_width = 1 if cell > 3 else 0

    def _on_resize(self, _event):
        # Coalesce the burst of events a window drag produces
        if self._after is not None:
            self.canvas.after_cancel(self._after)
        self._after = self.canvas.after(40, self._apply_size)

    def _apply_size(self):
        self._after = None
        cell = fit_cell_to(self.canvas.winfo_width(), self.canvas.winfo_height(),
                           self.gap)
        if cell != self.cell:
            self.relayout(cell)
            self._redraw()


def fit_window_to_screen(root, pref_w=1400, pref_h=1000):
    """Size and place the main window so it is entirely on screen."""
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    # Leave room for the title bar and the taskbar / dock / menu bar
    w = max(480, min(pref_w, sw - 40))
    h = max(360, min(pref_h, sh - 120))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2 - 30)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.minsize(min(w, 640), min(h, 420))


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

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 – Painter
# ─────────────────────────────────────────────────────────────────────────────
class PainterTab:
    GAP  = 20

    def __init__(self, parent, ensure_layout):
        self.frame = tk.Frame(parent, bg='#1a1a2e')
        self._ensure_layout = ensure_layout

        self.frames     = []
        self.frame_meta = []
        self.current    = 0
        self._undo      = []      # see _push_undo_*()
        self.dirty      = False   # unsaved changes since the last save / open

        self.draw_color  = (0, 255, 255)
        self.sound_mode  = tk.IntVar(value=SOUND_STATIC)
        self.timing_mode = tk.IntVar(value=TIMING_TIMED)
        self.duration_ms = tk.IntVar(value=500)
        self.snap_thresh = tk.IntVar(value=128)
        self.linear_m    = tk.DoubleVar(value=12.0)
        self.linear_b    = tk.DoubleVar(value=2.0)
        self.eraser      = tk.BooleanVar(value=False)
        self.mirror      = tk.BooleanVar(value=True)
        self.flip        = tk.BooleanVar(value=self._default_flip())
        self.show_marks  = tk.BooleanVar(value=True)
        self._marks      = {}     # flat → S / L text item on the canvas
        self._erasing    = False  # right-button stroke in progress

        self._build_canvas()
        self._build_controls()
        self.new_frame()
        self._undo.clear()
        self.dirty = False

    # ── Canvas ────────────────────────────────────────────────────────────────
    # The canvas is packed by _build_controls(), after the controls column,
    # so a narrow window squeezes the face rather than hiding the controls.
    @staticmethod
    def _default_flip():
        return bool(_D.mirror) and _D.mirror.get('flip', 'none') == 'h'

    def _build_mirror_maps(self):
        self._mirror_maps = {False: _D.mirror_map(flip='none'),
                             True:  _D.mirror_map(flip='h')}

    def _build_canvas(self):
        self._build_mirror_maps()
        self._fit = FittedLedCanvas(self.frame, self.GAP, self._draw_all)
        self.canvas = self._fit.canvas
        self.canvas.bind('<ButtonPress-1>', lambda e: self._start_stroke(e, False))
        self.canvas.bind('<B1-Motion>', self._on_paint)
        # Right button erases (Button-2 is the right button on macOS)
        for b in (2, 3):
            self.canvas.bind(f'<ButtonPress-{b}>', lambda e: self._start_stroke(e, True))
            self.canvas.bind(f'<B{b}-Motion>', self._on_paint)
            self.canvas.bind(f'<ButtonRelease-{b}>', self._end_erase)
        self.canvas.bind('<Motion>', self._on_hover)
        self.canvas.bind('<Leave>', lambda e: self._status_clear())
        self._canvas_items = {}
        self._item_to_flat = {}

    def rebuild_canvas(self, old_cfg):
        """Call after a display change to rebuild and redraw the canvas."""
        self._build_mirror_maps()
        self.flip.set(self._default_flip())
        self._mirror_check.config(state=tk.NORMAL if _D.mirror else tk.DISABLED)
        self._flip_check.config(state=tk.NORMAL if _D.mirror else tk.DISABLED)
        # Carry existing artwork across, matching panels by side and region
        self.frames = [remap_leds(leds, old_cfg, _D) for leds in self.frames]
        self._undo.clear()
        self._fit.relayout()
        self._fit._apply_size()
        if not self.frames:
            self.frames     = [blank_led_list()]
            self.frame_meta = [{'duration_ms': 500, 'timing_mode': TIMING_TIMED}]
            self.current    = 0
        self._build_fill_buttons()
        self._draw_all()

    # ── Status bar ────────────────────────────────────────────────────────────
    @staticmethod
    def _status_show(text):
        if help_ui.Tooltip.status is not None:
            help_ui.Tooltip.status.show(text)

    @staticmethod
    def _status_clear():
        if help_ui.Tooltip.status is not None:
            help_ui.Tooltip.status.clear()

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        # Scrolls when the window is too short to show every control
        scroll = ScrollFrame(self.frame, bg='#1a1a2e', width=240)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
        ctrl = scroll.inner
        self._ctrl = scroll
        BG = '#1a1a2e'

        def section(t, note=None):
            tk.Label(ctrl, text=t, bg=BG, fg='#e94560',
                     font=('Helvetica', 9, 'bold')).pack(anchor='w', pady=(10, 0))
            if note:
                tk.Label(ctrl, text=note, bg=BG, fg='#777', font=('Helvetica', 8),
                         wraplength=220, justify=tk.LEFT).pack(anchor='w')

        def label(t):
            w = tk.Label(ctrl, text=t, bg=BG, fg='#888')
            w.pack(anchor='w')
            return w

        def slider(var, lo, hi, res=1, command=None):
            w = tk.Scale(ctrl, from_=lo, to=hi, resolution=res, orient=tk.HORIZONTAL,
                         variable=var, bg=BG, fg='white', troughcolor='#16213e',
                         highlightthickness=0, command=command)
            w.pack(fill=tk.X)
            return w

        def check(t, var, text_tip, **kw):
            w = tk.Checkbutton(ctrl, text=t, variable=var, bg=BG, fg='white',
                               selectcolor='#16213e', **kw)
            w.pack(anchor='w')
            return tip(w, text_tip)

        # Brush
        section("BRUSH", "Used for the LEDs you paint next.")
        sw = tk.Frame(ctrl, bg=BG)
        sw.pack(fill=tk.X, pady=(4, 2))
        for i, rgb in enumerate(SWATCHES):
            s = tk.Label(sw, bg=hex_color(rgb), width=2, height=1, relief=tk.RAISED,
                         borderwidth=1, cursor='hand2')
            s.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
            s.bind('<Button-1>', lambda e, c=rgb: self.set_color(c))
            tip(s, f"Paint with this colour  (key {i + 1})")
        tip(tk.Button(ctrl, text="Pick Color…", command=self._pick_color,
                      bg='#16213e', fg='white'),
            "Choose any colour for the brush  (C)").pack(fill=tk.X, pady=2)
        self.color_preview = tk.Label(ctrl, bg=hex_color(self.draw_color), height=2,
                                      text="brush", fg='#000')
        self.color_preview.pack(fill=tk.X, pady=2)
        tip(self.color_preview, "The colour you're painting with")
        check("Eraser", self.eraser,
              "Paint LEDs black (off).  Right-click on the face erases too.  (E)",
              command=self._update_brush_preview)
        state = tk.NORMAL if _D.mirror else tk.DISABLED
        self._mirror_check = check(
            "Mirror to other side", self.mirror,
            "Also paint the matching LED on the other side of the face, so you "
            "only draw one half.  (M)", state=state)
        self._flip_check = check(
            "  Flip left↔right when mirroring", self.flip,
            "Make the other side a mirror image, like a real face.  Leave it on "
            "for Mk2 and Mk3.  If the right side is backwards on the helmet, "
            "turn it off.", state=state)
        check("Show S / L marks on sound LEDs", self.show_marks,
              "Mark LEDs that react to sound: S = Snap, L = Linear. Unmarked lit "
              "LEDs are Static (always on).", command=self._draw_all)

        # Sound
        section("SOUND REACTION", "How the LEDs you paint next react to the mic.")
        for lbl, val, text_tip in [
                ("Static (always on)", SOUND_STATIC,
                 "The LED always shows its colour. Use this for most of the face."),
                ("Snap (on when loud)", SOUND_SNAP,
                 "The LED is off when quiet and turns on when the volume reaches "
                 "the threshold below. Good for a talking mouth."),
                ("Linear (brighter when louder)", SOUND_LINEAR,
                 "The LED fades in as the volume goes up, set by m and b below.")]:
            tip(tk.Radiobutton(ctrl, text=lbl, variable=self.sound_mode, value=val,
                               bg=BG, fg='white', selectcolor='#16213e',
                               command=self._update_sound_controls),
                text_tip).pack(anchor='w')

        snap_l = label("Snap: on at volume (0-255):")
        snap_s = tip(slider(self.snap_thresh, 0, 255),
                     "Low (60) reacts to quiet sounds, high (200) needs shouting.")
        m_l = label("Linear m, how fast (0-15):")
        m_s = tip(slider(self.linear_m, 0, 15),
                  "1 fades in slowly over the whole volume range; 12 is fully "
                  "bright as soon as there's some sound.")
        b_l = label("Linear b, when silent (0-15):")
        b_s = tip(slider(self.linear_b, 0, 15),
                  "0 = off when silent, 15 = full brightness even when silent.")
        self._snap_widgets   = [snap_l, snap_s]
        self._linear_widgets = [m_l, m_s, b_l, b_s]

        # Timing
        section("FRAME TIMING", "Settings for the frame you're on. Every frame has its own.")
        for lbl, val, text_tip in [
                ("Timed (show for a set time)", TIMING_TIMED,
                 "Show this frame for the time below, then go to the next one."),
                ("Sound triggered (wait for sound)", TIMING_SOUND,
                 f"Stay on this frame until the volume goes past {SOUND_TRIGGER_LEVEL}, "
                 "then go to the next one.")]:
            tip(tk.Radiobutton(ctrl, text=lbl, variable=self.timing_mode, value=val,
                               bg=BG, fg='white', selectcolor='#16213e',
                               command=self._update_timing_controls),
                text_tip).pack(anchor='w')
        self._duration_label = label("")
        tip(slider(self.duration_ms, 0, 5000, 50, command=self._update_timing_controls),
            "1000 ms = 1 second.  A blink is about 100-150 ms per frame.")

        # Frames
        section("FRAMES")
        nav = tk.Frame(ctrl, bg=BG)
        nav.pack(fill=tk.X)
        tip(tk.Button(nav, text="◀ Prev", command=self.prev_frame, bg='#16213e',
                      fg='white', padx=2), "Previous frame  (←)").pack(side=tk.LEFT)
        tip(tk.Button(nav, text="Next ▶", command=self.next_frame, bg='#16213e',
                      fg='white', padx=2), "Next frame  (→)").pack(side=tk.RIGHT)
        self.frame_label = tk.Label(nav, text="Frame 1/1", bg=BG, fg='white')
        self.frame_label.pack(side=tk.LEFT, expand=True)

        row = tk.Frame(ctrl, bg=BG)
        row.pack(fill=tk.X, pady=(2, 0))
        for lbl, cmd, text_tip, bg in [
                ("+ New", self.new_frame,
                 "Add a blank frame at the end  (Ctrl+N)", '#16213e'),
                ("Duplicate", self.copy_frame,
                 "Copy this frame to the end, then change it a little. "
                 "The easiest way to animate.  (Ctrl+D)", '#16213e'),
                ("Delete", self.delete_frame, "Delete this frame", '#3d0000')]:
            tip(tk.Button(row, text=lbl, command=cmd, bg=bg, fg='white', padx=2),
                text_tip).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tip(tk.Button(ctrl, text="↶ Undo", command=self.undo, bg='#16213e', fg='white'),
            "Undo the last stroke, fill, clear or frame change  (Ctrl+Z)"
            ).pack(fill=tk.X, pady=(4, 0))

        # Fill — built dynamically to match current layout
        section("FILL REGION", "Fill a whole region with the brush.")
        self._ctrl_fill_frame = tk.Frame(ctrl, bg=BG)
        self._ctrl_fill_frame.pack(fill=tk.X)
        self._build_fill_buttons()

        # File
        section("FILE", "Save a .anim and copy it to the helmet's SD card. "
                        "Name it start.anim to play it at power-on.")
        tip(tk.Button(ctrl, text="Open .anim", command=self._open_anim,
                      bg='#0f3460', fg='white'),
            "Open a saved animation to edit it  (Ctrl+O)").pack(fill=tk.X, pady=2)
        tip(tk.Button(ctrl, text="Save .anim", command=self.save_anim,
                      bg='#0f3460', fg='white'),
            "Save all frames as a .anim file for the SD card  (Ctrl+S)").pack(fill=tk.X, pady=2)
        tip(tk.Button(ctrl, text="Load example", command=self.load_example,
                      bg='#0f3460', fg='white'),
            "Load a small example animation (blink, talking mouth, glowing nose) "
            "to see how one is made").pack(fill=tk.X, pady=2)
        tip(tk.Button(ctrl, text="Clear Frame", command=self.clear_frame,
                      bg='#3d0000', fg='white'),
            "Turn every LED in this frame off").pack(fill=tk.X, pady=2)

        self._update_sound_controls()
        self._update_timing_controls()

    def _update_sound_controls(self):
        """Grey out the sliders the chosen sound reaction doesn't use."""
        sm = self.sound_mode.get()
        for widgets, on in ((self._snap_widgets, sm == SOUND_SNAP),
                            (self._linear_widgets, sm == SOUND_LINEAR)):
            for w in widgets:
                w.config(state=tk.NORMAL if on else tk.DISABLED,
                         fg='white' if on and isinstance(w, tk.Scale) else
                            ('#888' if on else '#444'))
        self._update_brush_preview()

    def _update_timing_controls(self, _=None):
        ms = self.duration_ms.get()
        if self.timing_mode.get() == TIMING_TIMED:
            text = f"Show this frame for {ms} ms ({ms / 1000:g} s):"
        else:
            text = f"Stay at least {ms} ms, then wait for sound:"
        self._duration_label.config(text=text)

    def _update_brush_preview(self):
        if self.eraser.get():
            self.color_preview.config(bg='#000000', fg='#888', text="eraser")
            return
        r, g, b = self.draw_color
        fg = '#000' if (r * 299 + g * 587 + b * 114) > 128000 else '#fff'
        mode = {SOUND_STATIC: "always on", SOUND_SNAP: "on when loud",
                SOUND_LINEAR: "brighter when louder"}[self.sound_mode.get()]
        self.color_preview.config(bg=hex_color(self.draw_color), fg=fg,
                                  text=f"brush  ·  {mode}")

    def _build_fill_buttons(self):
        for w in self._ctrl_fill_frame.winfo_children():
            w.destroy()
        regions = [(label if r is None else label.title(), panels) for label, _s, r, panels in region_groups(_D)]
        regions.append(("All", list(_D.panels)))
        if _D.mirror:
            regions.append((f"Copy {_D.mirror['from']} → {_D.mirror['to']}", None))
        for lbl, panels in regions:
            cmd = (self._copy_side_to_other if panels is None
                   else (lambda p=panels: self._fill_panels(p)))
            text_tip = (f"Copy the whole {_D.mirror['from']} side onto the "
                        f"{_D.mirror['to']} side" if panels is None else
                        "Fill every LED on the display with the brush" if lbl == "All"
                        else f"Fill the {lbl.lower()} with the brush")
            tip(tk.Button(self._ctrl_fill_frame, text=lbl, command=cmd,
                          bg='#16213e', fg='white'), text_tip).pack(fill=tk.X, pady=1)

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw_all(self):
        self.canvas.delete('all')
        self._canvas_items.clear()
        self._item_to_flat.clear()

        leds = self.frames[self.current]
        for flat, coords in self._fit.rects.items():
            r, g, b = leds[flat][0], leds[flat][1], leds[flat][2]
            item = self.canvas.create_rectangle(
                *coords, fill=hex_color((r, g, b)), outline='#1a1a2e',
                width=self._fit.outline_width)
            self._canvas_items[flat] = item
            self._item_to_flat[item] = flat

        self._marks.clear()
        for flat in self._canvas_items:
            self._update_mark(flat)
        draw_region_outlines(self.canvas, self._fit.rects)
        self.frame_label.config(text=f"Frame {self.current+1}/{len(self.frames)}")

    def _update_cell(self, flat):
        if flat not in self._canvas_items:
            return
        r, g, b = self.frames[self.current][flat][:3]
        self.canvas.itemconfig(self._canvas_items[flat], fill=hex_color((r, g, b)))
        self._update_mark(flat)

    def _update_mark(self, flat):
        """Letter on a sound-reactive LED: S = Snap, L = Linear."""
        old = self._marks.pop(flat, None)
        if old is not None:
            self.canvas.delete(old)
        if not self.show_marks.get() or self._fit.cell < 9:
            return
        led = self.frames[self.current][flat]
        if led[3] not in (SOUND_SNAP, SOUND_LINEAR) or not any(led[:3]):
            return
        x1, y1, x2, y2 = self._fit.rects[flat]
        bright = led[0] * 299 + led[1] * 587 + led[2] * 114 > 128000
        self._marks[flat] = self.canvas.create_text(
            (x1 + x2) / 2, (y1 + y2) / 2, text='S' if led[3] == SOUND_SNAP else 'L',
            fill='#000' if bright else '#fff', state=tk.DISABLED,
            font=('Helvetica', max(6, self._fit.cell // 2), 'bold'))

    # ── Undo ──────────────────────────────────────────────────────────────────
    # Two kinds of entry:
    #   ('leds', frame, copy)                 a frame's pixels before a change;
    #                                          restored in place, so the frame
    #                                          object stays the same one
    #   ('frames', frames, meta, current)     the frame list before new /
    #                                          duplicate / delete
    def _push(self, entry):
        self._undo.append(entry)
        del self._undo[:-UNDO_LIMIT]
        self.dirty = True

    def _push_undo_leds(self):
        leds = self.frames[self.current]
        self._push(('leds', leds, [list(led) for led in leds]))

    def _push_undo_frames(self):
        self._save_meta()
        self._push(('frames', list(self.frames),
                    [dict(m) for m in self.frame_meta], self.current))

    def undo(self):
        if not self._undo:
            self._status_show("Nothing to undo.")
            return
        self._save_meta()
        entry = self._undo.pop()
        if entry[0] == 'leds':
            _kind, leds, saved = entry
            leds[:] = saved
            idx = next((i for i, f in enumerate(self.frames) if f is leds), None)
            if idx is not None:
                self.current = idx
        else:
            _kind, frames, meta, current = entry
            self.frames, self.frame_meta = frames, meta
            self.current = min(current, len(frames) - 1)
        self._load_meta()
        self._draw_all()

    # ── Input ─────────────────────────────────────────────────────────────────
    def _current_brush(self):
        if self.eraser.get() or self._erasing:
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
        each region reversed and x mirrored).
        """
        return self._mirror_maps[self.flip.get()].get(flat)

    def _flat_at(self, event):
        return next((self._item_to_flat[i]
                     for i in self.canvas.find_overlapping(event.x, event.y,
                                                           event.x, event.y)
                     if i in self._item_to_flat), None)

    def _start_stroke(self, event, erase):
        self.canvas.focus_set()          # so the keyboard shortcuts work
        self._erasing = erase
        self._stroke_saved = False       # undo point is made on the first paint
        self._on_paint(event)

    def _end_erase(self, _event=None):
        self._erasing = False

    def _on_hover(self, event):
        flat = self._flat_at(event)
        if flat is None:
            self._status_clear()
        else:
            self._status_show(f"{describe_pixel(flat)}   |   "
                              f"{describe_led(self.frames[self.current][flat])}")

    def _on_paint(self, event):
        flat = self._flat_at(event)
        if flat is None:
            return
        self._status_show(describe_pixel(flat))
        if not getattr(self, '_stroke_saved', True):
            self._push_undo_leds()
            self._stroke_saved = True
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
            new = {'duration_ms': self.duration_ms.get(),
                   'timing_mode': self.timing_mode.get()}
            if self.frame_meta[self.current] != new:
                self.frame_meta[self.current] = new
                self.dirty = True

    def _load_meta(self):
        m = self.frame_meta[self.current]
        self.duration_ms.set(m.get('duration_ms', 500))
        self.timing_mode.set(m.get('timing_mode', TIMING_TIMED))
        self._update_timing_controls()

    def new_frame(self):
        if self.frames:
            self._push_undo_frames()
        self.frames.append(blank_led_list())
        self.frame_meta.append({'duration_ms': 500, 'timing_mode': TIMING_TIMED})
        self.current = len(self.frames) - 1
        self._load_meta()
        self._draw_all()

    def copy_frame(self):
        self._push_undo_frames()
        self.frames.append(copy.deepcopy(self.frames[self.current]))
        self.frame_meta.append(dict(self.frame_meta[self.current]))
        self.current = len(self.frames) - 1
        self._load_meta()
        self._draw_all()

    def delete_frame(self):
        if len(self.frames) <= 1:
            messagebox.showinfo("Info", "Can't delete the only frame. "
                                "Use Clear Frame to turn its LEDs off.")
            return
        self._push_undo_frames()
        del self.frames[self.current]
        del self.frame_meta[self.current]
        self.current = max(0, self.current - 1)
        self._load_meta()
        self._draw_all()
        self._status_show("Frame deleted.  Ctrl+Z brings it back.")

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
        self._push_undo_leds()
        self.frames[self.current][:] = blank_led_list()
        self._draw_all()
        self._status_show("Frame cleared.  Ctrl+Z brings it back.")

    def _fill_panels(self, panels):
        self._push_undo_leds()
        brush = self._current_brush()
        leds  = self.frames[self.current]
        for p in panels:
            for i in range(p.offset, p.offset + p.count):
                leds[i] = list(brush)
        self._draw_all()

    def _copy_side_to_other(self):
        self._push_undo_leds()
        leds = self.frames[self.current]
        for p in _D.panels:
            if p.side != _D.mirror['from']:
                continue
            for src in range(p.offset, p.offset + p.count):
                dst = self._mirror_flat(src)
                if dst is not None:
                    leds[dst] = list(leds[src])
        self._draw_all()

    # ── Color ─────────────────────────────────────────────────────────────────
    def set_color(self, rgb):
        self.draw_color = tuple(rgb)
        self.eraser.set(False)          # picking a colour means "paint now"
        self._update_brush_preview()

    def pick_swatch(self, i):
        if 0 <= i < len(SWATCHES):
            self.set_color(SWATCHES[i])

    def _pick_color(self):
        result = colorchooser.askcolor(color=hex_color(self.draw_color),
                                       title="Pick brush color")
        if result and result[0]:
            self.set_color(int(v) for v in result[0])

    # ── File I/O ──────────────────────────────────────────────────────────────
    def confirm_discard(self, action):
        """Ask before `action` throws away unsaved work.  True = go ahead."""
        self._save_meta()
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Your animation has changes that aren't saved.\n\n"
            f"Save them before {action}?")
        if answer is None:
            return False
        if answer:
            return self.save_anim()
        return True

    def _open_anim(self):
        if not self.confirm_discard("opening another file"):
            return
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            anim = read_anim(path)
            loaded = anim.frames
            if not loaded:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(anim):
                return
            self.frames     = [[list(led) for led in fr['leds']] for fr in loaded]
            self.frame_meta = [{'duration_ms': fr['duration_ms'],
                                 'timing_mode': fr['timing_mode']} for fr in loaded]
            self.current = 0
            self._undo.clear()
            self._load_meta()
            self._draw_all()
            self.dirty = False
            messagebox.showinfo("Opened", f"Loaded {len(self.frames)} frames from\n{path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_example(self):
        if not self.confirm_discard("loading the example"):
            return
        self.frames, self.frame_meta = make_example(_D)
        self.current = 0
        self._undo.clear()
        self._load_meta()
        self._draw_all()
        self.dirty = False
        messagebox.showinfo(
            "Example loaded",
            "The example has 2 frames:\n\n"
            "• Frame 1: eyes open, shown for 2500 ms (Timed).\n"
            "• Frame 2: eyes closed, 150 ms. Together they make a blink.\n\n"
            "The mouth's S LEDs are Snap: they only light up when you talk, so "
            "the mouth opens. The nose's L LEDs are Linear: they glow brighter "
            "the louder you are.\n\n"
            "Hover over the LEDs to see their settings, and use Next ▶ to see "
            "frame 2. Then open the Simulator, press ▶ Auto Play and move the "
            "volume slider.")

    def save_anim(self):
        """Save to a file the user picks.  Returns True if it was saved."""
        self._save_meta()
        path = filedialog.asksaveasfilename(
            defaultextension=".anim",
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return False
        try:
            write_anim(path, _D, self.get_frames_data())
        except Exception as e:
            messagebox.showerror("Error", f"Could not save:\n{e}")
            return False
        self.dirty = False
        messagebox.showinfo(
            "Saved",
            f"{len(self.frames)} frames for \"{_D.name}\" → {path}\n\n"
            "Copy the file to the root of the helmet's SD card. Name it "
            "start.anim to play it when the helmet turns on.")
        return True

    def get_frames_data(self):
        """Return frames in the same dict format used by read_anim."""
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
        self.source = None        # None, 'painter' or 'file'

        self._build_canvas()
        self._build_controls()
        self._draw_blank()

    # ── Canvas ────────────────────────────────────────────────────────────────
    # The canvas is packed by _build_controls(), after the controls column.
    def _build_canvas(self):
        self._fit = FittedLedCanvas(self.frame, self.GAP, self._redraw)
        self.canvas = self._fit.canvas
        self._canvas_items = {}

    def _redraw(self):
        self._draw_blank()
        self._render_frame()

    def rebuild_canvas(self):
        """Call after layout change."""
        self._stop_play()
        self.frames = []
        self.source = None
        self._fit.relayout()
        self._draw_blank()
        self._fit._apply_size()
        self.file_label.config(text="No file loaded")
        self.frame_label.config(text="Frame -/-")
        self.timing_label.config(text="")

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        scroll = ScrollFrame(self.frame, bg='#0d0d1a', width=240)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
        ctrl = scroll.inner
        self._ctrl = scroll

        def section(t):
            tk.Label(ctrl, text=t, bg='#0d0d1a', fg='#e94560',
                     font=('Helvetica', 9, 'bold')).pack(anchor='w', pady=(10, 0))

        # File
        section("FILE")
        tip(tk.Button(ctrl, text="Preview Painter Frames", command=self.load_from_painter,
                      bg='#0f3460', fg='white'),
            "Play what you've painted, without saving first  (P)").pack(fill=tk.X, pady=2)
        tip(tk.Button(ctrl, text="Open .anim", command=self._open_file,
                      bg='#0f3460', fg='white'),
            "Play a saved .anim file").pack(fill=tk.X, pady=2)
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
        tip(tk.Button(nav, text="◀ Prev", command=self.prev_frame,
                      bg='#16213e', fg='white'),
            "Step back one frame  (←)").pack(side=tk.LEFT, expand=True, fill=tk.X)
        tip(tk.Button(nav, text="Next ▶", command=self.next_frame,
                      bg='#16213e', fg='white'),
            "Step forward one frame  (→)").pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.play_btn = tip(tk.Button(ctrl, text="▶ Auto Play", command=self.toggle_play,
                                      bg='#1a472a', fg='white'),
                            "Play with the same timing and sound rules as the helmet  (Space)")
        self.play_btn.pack(fill=tk.X, pady=2)

        # Audio
        section("AUDIO INPUT")
        mic_row = tk.Frame(ctrl, bg='#0d0d1a')
        mic_row.pack(fill=tk.X)
        self.mic_btn = tip(tk.Button(mic_row, text="🎤 Use Mic",
                                     command=self._toggle_mic,
                                     bg='#16213e', fg='white'),
                           "Use your computer's microphone as the helmet's mic")
        self.mic_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        if not PYAUDIO_OK:
            tk.Label(mic_row, text="(pyaudio not installed)",
                     bg='#0d0d1a', fg='#e94560', font=('Helvetica', 7)).pack()

        tk.Label(ctrl, text="Manual volume (0-255):",
                 bg='#0d0d1a', fg='#888').pack(anchor='w', pady=(6, 0))
        self.vol_slider = tip(tk.Scale(ctrl, from_=0, to=255, orient=tk.HORIZONTAL,
                                   variable=self.vol_override, command=self._on_slider,
                                   bg='#0d0d1a', fg='white', troughcolor='#16213e',
                                   highlightthickness=0),
                              "Pretend there's sound: drag right to see Snap and Linear "
                              "LEDs react and sound-triggered frames advance")
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
                 text="Snap LEDs turn on when the volume\n"
                      "reaches their threshold.\n"
                      "Linear LEDs get brighter as it rises.\n"
                      "Sound-triggered frames advance\n"
                      f"when the volume rises past {SOUND_TRIGGER_LEVEL}.",
                 bg='#0d0d1a', fg='#666', font=('Helvetica', 8),
                 justify=tk.LEFT).pack(anchor='w', pady=8)

    # ── File loading ──────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            anim = read_anim(path)
            frames = anim.frames
            if not frames:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(anim):
                return
            self._load_frames(frames)
            self.source = 'file'
            self.file_label.config(text=os.path.basename(path))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_from_painter(self):
        frames = self._get_painter_frames()
        if not frames:
            messagebox.showinfo("Info", "No frames in Painter yet.")
            return
        self._load_frames(frames)
        self.source = 'painter'
        self.file_label.config(text="[Painter frames]")

    def on_shown(self):
        """The tab was opened: show the Painter's latest frames, unless a file
        is loaded or something is playing."""
        if self.source != 'file' and not self.playing:
            frames = self._get_painter_frames()
            if frames:
                current = self.current
                self._load_frames(frames)
                self.current = min(current, len(frames) - 1)
                self.source = 'painter'
                self.file_label.config(text="[Painter frames]")
                self._render_frame()

    def _load_frames(self, frames):
        self._stop_play()
        self.frames  = frames
        self.current = 0
        self._render_frame()

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _draw_blank(self):
        self.canvas.delete('all')
        self._canvas_items.clear()
        for flat, coords in self._fit.rects.items():
            item = self.canvas.create_rectangle(*coords, fill='#111122',
                                                outline='#1a1a2e',
                                                width=self._fit.outline_width)
            self._canvas_items[flat] = item
        draw_region_outlines(self.canvas, self._fit.rects)
        if not self.frames:
            w = max(self.canvas.winfo_width(), 200)
            h = max(self.canvas.winfo_height(), 100)
            self.canvas.create_text(
                w // 2, h // 2, fill='#9a9ab0', font=('Helvetica', 13), justify=tk.CENTER,
                width=max(200, w - 40),
                text="Nothing to play yet.\n\nClick \"Preview Painter Frames\" to play "
                     "what you've painted,\nor \"Open .anim\" to play a saved file.",
                tags='empty_hint')
            x1, y1, x2, y2 = self.canvas.bbox('empty_hint')
            self.canvas.tag_lower(self.canvas.create_rectangle(
                x1 - 16, y1 - 12, x2 + 16, y2 + 12, fill='#0d0d1a', outline='#2a2a4a',
                tags='empty_hint'), 'empty_hint')

    def _render_frame(self):
        if not self.frames:
            return
        self.canvas.delete('empty_hint')
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
    def toggle_play(self):
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
        scroll = ScrollFrame(self.frame, bg='#1a1a2e')
        scroll.pack(fill=tk.BOTH, expand=True)
        f = scroll.inner
        BG, FG, ACC = '#1a1a2e', 'white', '#e94560'

        def section(t):
            tk.Label(f, text=t, bg=BG, fg=ACC,
                     font=('Helvetica', 10, 'bold')).pack(anchor='w', padx=12, pady=(14, 2))

        tk.Label(f, text="Most people don't need this tab. To put an animation on the "
                         "helmet, save a .anim in the Painter and copy it to the SD card.\n"
                         "This tab is for building the firmware yourself: it turns one frame "
                         "into fallback_anim.h, the face built into the firmware and shown "
                         "when there's no SD card or no start.anim.",
                 bg=BG, fg='#9a9ab0', justify=tk.LEFT, wraplength=640,
                 font=('Helvetica', 9)).pack(anchor='w', padx=12, pady=(12, 0))

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
            anim = read_anim(path)
            frames = anim.frames
            if not frames:
                messagebox.showerror("Error", "No valid frames found.")
                return
            if not self._ensure_layout(anim):
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
        D     = _D

        counts = {0: 0, 1: 0, 2: 0}
        for led in leds:
            sm = led[3]
            if sm in counts:
                counts[sm] += 1

        timing_str = (f"SOUND_TRIGGERED (hold ≥{dur} ms)" if tm == TIMING_SOUND
                      else f"TIMED ({dur} ms)")
        lines = [
            f"Display    : {D.name}  ({len(D.panels)} panels, {D.total_pixels} pixels)",
            f"Frame      : {idx + 1} of {len(self._anim_frames)}",
            f"Timing     : {timing_str}",
            f"LEDs       : {counts[0]} STATIC  |  {counts[1]} SNAP  |  {counts[2]} LINEAR",
            "",
        ]

        # Lit-LED count per region, so an empty side is obvious before export
        for label, _side, _region, panels in region_groups(D):
            total = sum(p.count for p in panels)
            lit = sum(1 for p in panels for i in range(p.offset, p.offset + p.count)
                      if any(leds[i][:3]))
            lines.append(f"  {label + ' lit':<18}: {lit}/{total}")

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
                f"Frame has {len(frame['leds'])} LEDs but the display "
                f"\"{_D.name}\" has {TOTAL_LEDS()}. Reload the source.")
            return
        try:
            self._generate_h(frame, idx, total, self._source_name, out_path)
        except OSError as e:
            messagebox.showerror("Error", f"Could not write file:\n{e}")
            return
        self.status_label.config(
            text=f"✓  Written → {out_path}   (frame {idx+1}/{total}, "
                 f"display {_D.name})")

    def _generate_h(self, frame, frame_idx, total_frames, source_name, out_path):
        D      = _D
        total  = D.total_pixels
        v1     = next((n for n in (11, 14) if D.is_v1_compatible(n)), 0)
        dur    = frame['duration_ms']
        timing = frame['timing_mode']
        leds   = frame['leds']
        timing_str = "SOUND_TRIGGERED" if timing == TIMING_SOUND else f"TIMED ({dur}ms)"


        lines = [
            '#pragma once',
            '#include <stdint.h>',
            '#include <string.h>',
            '',
            '// ── Fallback animation ─────────────────────────────────────────────────────',
            f'// Source    : {source_name}',
            f'// Frame     : {frame_idx + 1} of {total_frames}',
            f'// Timing    : {timing_str}',
            f'// Display   : {D.name}  ({len(D.panels)} panels, {total} pixels, layout ID {D.layout_id():08x})',
            '//',
            '// sound_mode : 0=STATIC  1=SNAP  2=LINEAR',
            '// param      : SNAP   → threshold 0-255',
            '//              LINEAR → high nibble=m (0-15)  low nibble=b (0-15)',
            '//',
            '// The firmware checks these against its display and refuses to build',
            '// if they differ.  FALLBACK_LAYOUT is the old 11/14 panel count',
            '// (0 for other displays).',
            '',
            f'#define FALLBACK_LAYOUT    {v1}',
            f'#define FALLBACK_PIXELS    {total}',
            f'#define FALLBACK_LAYOUT_ID 0x{D.layout_id():08X}u',
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

        # ── Display selector toolbar ─────────────────────────────────────────
        toolbar = tk.Frame(root, bg='#0d0d1a', pady=4)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(toolbar, text="Display:", bg='#0d0d1a', fg='#aaa',
                 font=('Helvetica', 9, 'bold')).pack(side=tk.LEFT, padx=(10, 4))

        self._display_var = tk.StringVar(value=_D.name)
        self._display_box = ttk.Combobox(toolbar, textvariable=self._display_var,
                                         values=list(DISPLAYS), state='readonly', width=22)
        self._display_box.pack(side=tk.LEFT, padx=4)
        self._display_box.bind('<<ComboboxSelected>>', self._on_display_selected)
        tip(self._display_box, "The LED layout of your helmet. It must match the display "
                               "the firmware is set up for. Pick it before you start.")
        tip(tk.Button(toolbar, text="Open display.json…", command=self._open_display,
                      bg='#16213e', fg='white'),
            "Load a display that isn't in the list (HUB75, MAX7219, your own LED board) "
            "from its display.json").pack(side=tk.LEFT, padx=4)
        tip(tk.Button(toolbar, text="? Help", command=lambda: self.help.toggle(),
                      bg='#e94560', fg='white', font=('Helvetica', 9, 'bold'), padx=10),
            "Open the guide: getting started, what every setting does, "
            "shortcuts  (F1)").pack(side=tk.RIGHT, padx=10)

        self._layout_info = tk.Label(toolbar, text=self._layout_info_text(),
                                     bg='#0d0d1a', fg='#888', font=('Helvetica', 8),
                                     anchor='w')
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

        # Hint line at the bottom (packed before the notebook so it keeps its space)
        self.status = help_ui.StatusBar(root)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        help_ui.Tooltip.status = self.status

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True)
        self.nb = nb

        self.painter  = PainterTab(nb, self.ensure_layout)
        self.sim      = SimulatorTab(nb, self.painter.get_frames_data, self.ensure_layout)
        self.exporter = ExportTab(nb, self.painter.get_frames_data, self.ensure_layout)

        nb.add(self.painter.frame,  text="  🎨  Painter  ")
        nb.add(self.sim.frame,      text="  ▶   Simulator  ")
        nb.add(self.exporter.frame, text="  📄  Export .h  ")
        self._tabs = [self.painter.frame, self.sim.frame, self.exporter.frame]
        nb.bind('<<NotebookTabChanged>>', self._on_tab_changed)

        # Help overlay, on top of everything else in the window
        self.settings = help_ui.Settings()
        self.help = help_ui.HelpOverlay(root, self.settings, actions={
            'load_example': lambda: (self.nb.select(0), self.painter.load_example())})
        self._bind_shortcuts()

        fit_window_to_screen(root)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._mic_refresh()
        self._on_tab_changed()
        # The guide opens by itself the first time, then only if asked to
        if self.settings.get('show_guide_at_start') or not self.settings.get('guide_seen'):
            root.after(300, lambda: self.help.show(0))
            self.settings.set('guide_seen', True)

    # ── Tabs and hints ────────────────────────────────────────────────────────
    TAB_HINTS = [
        "Painter: click or drag on the face to paint, right-click to erase. "
        "Hover over any button to see what it does.",
        "Simulator: click Preview Painter Frames (or Open .anim), then ▶ Auto Play. "
        "Move the volume slider to test sound reactions.",
        "Export .h: only for building the firmware yourself. To use an animation, "
        "save a .anim and copy it to the SD card.",
    ]

    def current_tab(self):
        try:
            return self._tabs.index(self.root.nametowidget(self.nb.select()))
        except (ValueError, KeyError, tk.TclError):
            return 0

    def _on_tab_changed(self, _event=None):
        tab = self.current_tab()
        self.status.set_default(self.TAB_HINTS[tab])
        if tab == 1:
            self.sim.on_shown()

    # ── Keyboard shortcuts (listed in help_ui.SHORTCUTS) ──────────────────────
    _TYPING = ('Entry', 'Spinbox', 'Text', 'TEntry', 'TCombobox', 'TSpinbox')

    def _bind_shortcuts(self):
        mod = 'Command' if sys.platform == 'darwin' else 'Control'
        P, S = 0, 1     # tab numbers

        # One root binding per key; several tabs can use the same key
        self._keymap = {}

        def bind(seqs, fn, tab=None, letter=False):
            for seq in seqs:
                if seq not in self._keymap:
                    self._keymap[seq] = []
                    self.root.bind(seq, lambda e, seq=seq: self._shortcut(e, seq))
                self._keymap[seq].append((fn, tab, letter))

        def ctrl(key):
            return [f'<{mod}-{key}>', f'<{mod}-{key.upper()}>']

        def key(k):
            return [f'<KeyPress-{k}>', f'<KeyPress-{k.upper()}>'] if k.isalpha() \
                else [f'<KeyPress-{k}>']

        self.root.bind('<F1>', lambda e: (self.help.toggle(), 'break')[1])
        self.root.bind('<Escape>', lambda e: self.help.hide() if self.help.visible else None)
        for i in range(3):
            bind([f'<{mod}-Key-{i + 1}>'], lambda i=i: self.nb.select(i))

        p = self.painter
        bind(ctrl('z'), p.undo, P)
        bind(ctrl('n'), p.new_frame, P)
        bind(ctrl('d'), p.copy_frame, P)
        bind(ctrl('o'), p._open_anim, P)
        bind(ctrl('s'), p.save_anim, P)
        bind(['<Left>'], p.prev_frame, P, letter=True)
        bind(['<Right>'], p.next_frame, P, letter=True)
        bind(key('e'), lambda: (p.eraser.set(not p.eraser.get()),
                                p._update_brush_preview()), P, letter=True)
        bind(key('m'), lambda: p.mirror.set(not p.mirror.get()) if _D.mirror else None,
             P, letter=True)
        bind(key('c'), p._pick_color, P, letter=True)
        for i in range(len(SWATCHES)):
            bind(key(str(i + 1)), lambda i=i: p.pick_swatch(i), P, letter=True)

        s = self.sim
        bind(['<space>'], s.toggle_play, S, letter=True)
        bind(['<Left>'], s.prev_frame, S, letter=True)
        bind(['<Right>'], s.next_frame, S, letter=True)
        bind(key('p'), s.load_from_painter, S, letter=True)

    def _shortcut(self, _event, seq):
        if self.help.visible:
            return None
        focus = self.root.focus_get()
        cls = focus.winfo_class() if focus is not None else ''
        if cls in self._TYPING:
            return None        # let the user type
        tab = self.current_tab()
        for fn, want_tab, letter in self._keymap[seq]:
            if want_tab is not None and want_tab != tab:
                continue
            if letter and cls == 'Scale':
                return None    # arrow keys move a selected slider
            fn()
            return 'break'
        return None

    def _layout_info_text(self):
        D = _D
        outs = ', '.join(f"{o['type']}" + (f" GP{o['pin']}" if 'pin' in o else '')
                         for o in D.outputs)
        return (f"{len(D.panels)} panels, {D.total_pixels} pixels  |  outputs: {outs}  "
                f"|  must match the display the firmware is set up for")

    def _on_display_selected(self, _event=None):
        cfg = DISPLAYS[self._display_var.get()]
        if cfg is _D:
            return
        self.root.focus_set()     # so the shortcut keys go to the tool again
        if not messagebox.askyesno(
                "Switch display?",
                f"Switch to \"{cfg.name}\"?\n\n"
                "Painter frames are converted panel by panel (eyes → eyes, "
                "mouth → mouth, nose → nose); panels that don't exist on the "
                "new display are dropped.\nContinue?"):
            self._display_var.set(_D.name)
            return
        self._apply_display(cfg)

    def _open_display(self):
        path = filedialog.askopenfilename(
            title="Open display config",
            filetypes=[("Display config", "*.json"), ("All", "*.*")])
        if not path:
            return None
        try:
            cfg = dcfg.load(path)
        except (dcfg.ConfigError, OSError) as e:
            messagebox.showerror("Display config", f"Can't use {os.path.basename(path)}:\n\n{e}")
            return None
        DISPLAYS[cfg.name] = cfg
        self._display_box.config(values=list(DISPLAYS))
        if cfg is not _D:
            self._apply_display(cfg)
        return cfg

    def _apply_display(self, cfg):
        old = _D
        set_display(cfg)
        self._display_box.config(values=list(DISPLAYS))
        self._display_var.set(cfg.name)
        self._layout_info.config(text=self._layout_info_text())
        # Rebuild canvases and reset per-display state
        self.painter.rebuild_canvas(old)
        self.sim.rebuild_canvas()
        self.exporter.reset()

    def ensure_layout(self, anim):
        """Make the active display match a loaded .anim.  Returns True if it now does."""
        if anim.fits(_D):
            return True
        match = next((c for c in DISPLAYS.values() if anim.fits(c)), None)
        if match is not None:
            if not messagebox.askyesno(
                    "Display mismatch",
                    f"This file was made for \"{match.name}\" but the tool is set "
                    f"to \"{_D.name}\".\n\nSwitch to \"{match.name}\"?"):
                return False
            self._apply_display(match)
            return True
        if not messagebox.askyesno(
                "Display mismatch",
                f"This file was made for {anim.describe()}, which doesn't match "
                f"any display the tool has loaded.\n\nOpen its display.json?"):
            return False
        cfg = self._open_display()
        if cfg is None:
            return False
        if not anim.fits(cfg):
            messagebox.showerror("Display mismatch",
                                 f"\"{cfg.name}\" doesn't match this file either.")
            return False
        return True

    def _mic_refresh(self):
        self.sim.mic_refresh()
        self.root.after(33, self._mic_refresh)

    def _on_close(self):
        if not self.painter.confirm_discard("closing"):
            return
        self.sim.on_close()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
