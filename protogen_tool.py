#!/usr/bin/env python3
"""
protogen_tool.py  —  Unified Protogen animation tool
Combines:
  • Face Painter  (draw LED animations, save .anim)
  • Simulator     (play back .anim with sound-reaction preview)
  • Export to .h  (convert any frame from a .anim to fallback_anim.h)

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

# ── Shared constants ──────────────────────────────────────────────────────────
MAGIC          = b'ANIM'
VERSION        = 0x01
PANELS         = 14
LEDS_PER_PANEL = 64
TOTAL_LEDS     = PANELS * LEDS_PER_PANEL

SOUND_STATIC = 0
SOUND_SNAP   = 1
SOUND_LINEAR = 2
TIMING_TIMED = 0
TIMING_SOUND = 1

PANEL_EYE_L = 0
PANEL_EYE_R = 1
PANEL_MOUTH = [2, 3, 4, 5]
PANEL_NOSE  = 6

# ── Shared helpers ────────────────────────────────────────────────────────────
def panel_offset(p):
    return p * LEDS_PER_PANEL

def xy_to_led_idx(panel, x, y):
    return panel_offset(panel) + y * 8 + x

def pack_linear(m, b):
    return (max(0, min(15, int(m))) << 4) | max(0, min(15, int(b)))

def blank_led_list():
    return [[0, 0, 0, SOUND_STATIC, 0] for _ in range(TOTAL_LEDS)]

def hex_color(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

# ── Binary I/O ────────────────────────────────────────────────────────────────
def make_frame_bytes(duration_ms, timing_mode, leds):
    """Pack one animation frame into bytes."""
    assert len(leds) == TOTAL_LEDS
    data = struct.pack('<HBB', duration_ms, timing_mode, 0)
    for (r, g, b, sm, p) in leds:
        data += struct.pack('BBBBB', r, g, b, sm, p)
    return data

def write_anim(path, frames_bytes):
    with open(path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('BBBB', VERSION, PANELS, 0, 0))
        for frame in frames_bytes:
            f.write(frame)
    print(f"Wrote {len(frames_bytes)} frames → {path}")

def load_anim(path):
    """
    Returns (version, panels, frames) where each frame is:
      {'duration_ms': int, 'timing_mode': int, 'leds': list-of-[r,g,b,sm,p]}
    """
    frames = []
    with open(path, 'rb') as f:
        hdr = f.read(8)
        if len(hdr) < 8 or hdr[:4] != b'ANIM':
            raise ValueError("Not a valid .anim file")
        version = hdr[4]
        panels  = hdr[5]
        while True:
            fhdr = f.read(4)
            if len(fhdr) < 4:
                break
            duration_ms = struct.unpack_from('<H', fhdr, 0)[0]
            timing_mode = fhdr[2]
            leds = []
            for _ in range(TOTAL_LEDS):
                entry = f.read(5)
                if len(entry) < 5:
                    break
                leds.append(list(entry))
            if len(leds) == TOTAL_LEDS:
                frames.append({
                    'duration_ms': duration_ms,
                    'timing_mode': timing_mode,
                    'leds': leds,
                })
    return version, panels, frames

# ── Shared canvas layout builder ──────────────────────────────────────────────
def build_led_rects(cell, gap):
    """
    Returns a dict: flat_led_index → (x1, y1, x2, y2) canvas coords.
    Also returns (canvas_w, canvas_h).
    """
    mouth_ox = cell * 1 + gap
    mouth_oy = gap + 8 * cell + gap
    eye_ox   = mouth_ox - cell
    eye_oy   = mouth_oy - gap - 8 * cell
    nose_ox  = mouth_ox + 32 * cell
    nose_oy  = eye_oy

    canvas_w = nose_ox + 8 * cell + gap
    canvas_h = mouth_oy + 8 * cell + gap * 2

    rects = {}

    # Eye (2 panels)
    for panel, px_off in [(PANEL_EYE_L, 0), (PANEL_EYE_R, 8 * cell)]:
        for y in range(8):
            for x in range(8):
                flat = xy_to_led_idx(panel, x, y)
                x1 = eye_ox + px_off + x * cell
                y1 = eye_oy + y * cell
                rects[flat] = (x1, y1, x1 + cell - 1, y1 + cell - 1)

    # Mouth (4 panels)
    for pi, panel in enumerate(PANEL_MOUTH):
        for y in range(8):
            for x in range(8):
                flat = xy_to_led_idx(panel, x, y)
                x1 = mouth_ox + (pi * 8 + x) * cell
                y1 = mouth_oy + y * cell
                rects[flat] = (x1, y1, x1 + cell - 1, y1 + cell - 1)

    # Nose (1 panel)
    for y in range(8):
        for x in range(8):
            flat = xy_to_led_idx(PANEL_NOSE, x, y)
            x1 = nose_ox + x * cell
            y1 = nose_oy + y * cell
            rects[flat] = (x1, y1, x1 + cell - 1, y1 + cell - 1)

    return rects, canvas_w, canvas_h

def draw_region_outlines(canvas, led_rects):
    """Draw colored outlines around eye / mouth / nose regions."""
    for panels, color, label in [
        ([PANEL_EYE_L, PANEL_EYE_R], '#4fc3f7', 'EYE'),
        (PANEL_MOUTH,                '#e94560', 'MOUTH'),
        ([PANEL_NOSE],               '#a5d6a7', 'NOSE'),
    ]:
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
            return False

    def stop(self):
        self.running = False
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa:
            self._pa.terminate()
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

    def __init__(self, parent):
        self.frame = tk.Frame(parent, bg='#1a1a2e')

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

        self._build_canvas()
        self._build_controls()
        self.new_frame()

    # ── Canvas ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        self._led_rects, cw, ch = build_led_rects(self.CELL, self.GAP)
        self.canvas = tk.Canvas(self.frame, width=cw, height=ch,
                                bg='#0d0d1a', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        self.canvas.bind('<Button-1>', self._on_paint)
        self.canvas.bind('<B1-Motion>', self._on_paint)
        self._canvas_items = {}

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        ctrl = tk.Frame(self.frame, bg='#1a1a2e', width=230)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        ctrl.pack_propagate(False)

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
        tk.Label(ctrl, text="Duration ms:", bg='#1a1a2e', fg='#888').pack(anchor='w')
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

        # Fill
        section("FILL REGION")
        for lbl, panels in [("Eye",   [PANEL_EYE_L, PANEL_EYE_R]),
                             ("Mouth", PANEL_MOUTH),
                             ("Nose",  [PANEL_NOSE]),
                             ("All",   list(range(PANELS)))]:
            tk.Button(ctrl, text=lbl,
                      command=lambda p=panels: self._fill_panels(p),
                      bg='#16213e', fg='white').pack(fill=tk.X, pady=1)

        # File
        section("FILE")
        tk.Button(ctrl, text="Open .anim", command=self._open_anim,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        tk.Button(ctrl, text="Save .anim", command=self.save_anim,
                  bg='#0f3460', fg='white').pack(fill=tk.X, pady=2)
        tk.Button(ctrl, text="Clear Frame", command=self.clear_frame,
                  bg='#3d0000', fg='white').pack(fill=tk.X, pady=2)

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw_all(self):
        self.canvas.delete('all')
        self._canvas_items.clear()

        leds = self.frames[self.current]
        for flat, coords in self._led_rects.items():
            r, g, b = leds[flat][0], leds[flat][1], leds[flat][2]
            item = self.canvas.create_rectangle(
                *coords, fill=hex_color((r, g, b)), outline='#1a1a2e', width=1)
            self._canvas_items[flat] = item

        draw_region_outlines(self.canvas, self._led_rects)
        self.frame_label.config(text=f"Frame {self.current+1}/{len(self.frames)}")

    def _update_cell(self, flat):
        if flat not in self._canvas_items:
            return
        r, g, b = self.frames[self.current][flat][:3]
        self.canvas.itemconfig(self._canvas_items[flat], fill=hex_color((r, g, b)))

    # ── Input ─────────────────────────────────────────────────────────────────
    def _on_paint(self, event):
        flat = next((i for i, (x1, y1, x2, y2) in self._led_rects.items()
                     if x1 <= event.x <= x2 and y1 <= event.y <= y2), None)
        if flat is None:
            return
        leds = self.frames[self.current]
        if self.eraser.get():
            leds[flat] = [0, 0, 0, SOUND_STATIC, 0]
        else:
            sm = self.sound_mode.get()
            param = (self.snap_thresh.get() if sm == SOUND_SNAP else
                     pack_linear(self.linear_m.get(), self.linear_b.get())
                     if sm == SOUND_LINEAR else 0)
            r, g, b = self.draw_color
            leds[flat] = [r, g, b, sm, param]
        self._update_cell(flat)

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
        self._save_meta() if self.frames else None
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
        sm = self.sound_mode.get()
        param = (self.snap_thresh.get() if sm == SOUND_SNAP else
                 pack_linear(self.linear_m.get(), self.linear_b.get())
                 if sm == SOUND_LINEAR else 0)
        r, g, b = self.draw_color
        leds = self.frames[self.current]
        for p in panels:
            base = panel_offset(p)
            for i in range(LEDS_PER_PANEL):
                leds[base + i] = [r, g, b, sm, param]
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
            _, _, loaded = load_anim(path)
            if not loaded:
                messagebox.showerror("Error", "No valid frames found.")
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
        binary_frames = [
            make_frame_bytes(self.frame_meta[i]['duration_ms'],
                             self.frame_meta[i]['timing_mode'],
                             [(l[0], l[1], l[2], l[3], l[4]) for l in leds])
            for i, leds in enumerate(self.frames)
        ]
        write_anim(path, binary_frames)
        messagebox.showinfo("Saved", f"{len(binary_frames)} frames → {path}")

    def get_frames_data(self):
        """Return frames in the same dict format used by load_anim, for sharing with other tabs."""
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

    def __init__(self, parent, get_painter_frames):
        """
        get_painter_frames: callable that returns the Painter's current frames,
                            so the Simulator can preview what you're painting.
        """
        self.frame = tk.Frame(parent, bg='#0d0d1a')
        self._get_painter_frames = get_painter_frames

        self.frames      = []
        self.current     = 0
        self.playing     = False
        self.mic_active  = False
        self.mic         = MicMonitor()
        self.vol_override = tk.IntVar(value=0)
        self._play_after = None

        self._build_canvas()
        self._build_controls()
        self._draw_blank()

    # ── Canvas ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        self._led_rects, cw, ch = build_led_rects(self.CELL, self.GAP)
        self.canvas = tk.Canvas(self.frame, width=cw, height=ch,
                                bg='#0d0d1a', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        self._canvas_items = {}

    # ── Controls ─────────────────────────────────────────────────────────────
    def _build_controls(self):
        ctrl = tk.Frame(self.frame, bg='#0d0d1a', width=240)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        ctrl.pack_propagate(False)

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
                 text="SNAP   = flashes on threshold\nLINEAR = y=mx+b brightness",
                 bg='#0d0d1a', fg='#666', font=('Helvetica', 8),
                 justify=tk.LEFT).pack(anchor='w', pady=8)

    # ── File loading ──────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            _, _, frames = load_anim(path)
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
        draw_region_outlines(self.canvas, self._led_rects)

    def _render_frame(self):
        if not self.frames:
            return
        frame = self.frames[self.current]
        vol   = self._get_volume()

        vu_w  = int(self.vu_canvas.winfo_width() * vol / 255)
        color = '#00e676' if vol < 180 else '#ff5252'
        self.vu_canvas.coords(self.vu_bar, 0, 2, vu_w, 14)
        self.vu_canvas.itemconfig(self.vu_bar, fill=color)

        tm = "TIMED" if frame['timing_mode'] == TIMING_TIMED else "SOUND TRIGGERED"
        ms = frame['duration_ms'] if frame['timing_mode'] == TIMING_TIMED else "∞"
        self.frame_label.config(text=f"Frame {self.current+1}/{len(self.frames)}")
        self.timing_label.config(text=f"{tm}  |  {ms}ms  |  vol={vol}")

        for flat, item in self._canvas_items.items():
            if flat >= TOTAL_LEDS:
                continue
            led   = frame['leds'][flat]
            r, g, b = apply_sound(led, vol)
            self.canvas.itemconfig(item, fill='#{:02x}{:02x}{:02x}'.format(r, g, b))

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
        self._play_tick()

    def _stop_play(self):
        self.playing = False
        self.play_btn.config(text="▶ Auto Play", bg='#1a472a')
        if self._play_after:
            self.frame.after_cancel(self._play_after)
            self._play_after = None

    def _play_tick(self):
        if not self.playing or not self.frames:
            return
        self._render_frame()
        frame = self.frames[self.current]
        if frame['timing_mode'] == TIMING_TIMED:
            ms = max(16, frame['duration_ms'])
            self._play_after = self.frame.after(ms, self._advance_and_tick)
        else:
            self._play_after = self.frame.after(33, self._play_tick)

    def _advance_and_tick(self):
        if not self.playing:
            return
        self.current = (self.current + 1) % len(self.frames)
        self._play_tick()

    def next_frame(self):
        if not self.frames:
            return
        self._stop_play()
        self.current = min(self.current + 1, len(self.frames) - 1)
        self._render_frame()

    def prev_frame(self):
        if not self.frames:
            return
        self._stop_play()
        self.current = max(self.current - 1, 0)
        self._render_frame()

    def mic_refresh(self):
        if self.mic_active and not self.playing:
            self._render_frame()

    def on_close(self):
        self.mic.stop()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 – Export to .h
# ─────────────────────────────────────────────────────────────────────────────
class ExportTab:
    def __init__(self, parent, get_painter_frames):
        self.frame = tk.Frame(parent, bg='#1a1a2e')
        self._get_painter_frames = get_painter_frames

        self._anim_frames = []   # list of frame dicts
        self._source_name = ""
        self._chosen_idx  = tk.IntVar(value=0)

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
        self.preview_text = tk.Text(f, height=8, bg='#0d0d1a', fg='#ccc',
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

        # Export
        tk.Button(f, text="Export fallback_anim.h", command=self._export,
                  bg='#0f3460', fg=FG,
                  font=('Helvetica', 10, 'bold')).pack(pady=14, padx=12, fill=tk.X)

        self.status_label = tk.Label(f, text="", bg=BG, fg='#a5d6a7',
                                     font=('Helvetica', 9))
        self.status_label.pack(anchor='w', padx=12)

    # ── Load source ───────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Anim files", "*.anim"), ("All", "*.*")])
        if not path:
            return
        try:
            _, _, frames = load_anim(path)
            if not frames:
                messagebox.showerror("Error", "No valid frames found.")
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

    # ── Preview ───────────────────────────────────────────────────────────────
    def _update_preview(self):
        if not self._anim_frames:
            return
        try:
            idx = max(1, min(len(self._anim_frames), int(self._chosen_idx.get()))) - 1
        except (ValueError, tk.TclError):
            return
        frame = self._anim_frames[idx]
        dur   = frame['duration_ms']
        tm    = frame['timing_mode']
        leds  = frame['leds']

        counts = {0: 0, 1: 0, 2: 0}
        for led in leds:
            sm = led[3]
            if sm in counts:
                counts[sm] += 1

        timing_str = "SOUND_TRIGGERED" if tm == 1 else f"TIMED ({dur} ms)"
        lines = [
            f"Frame      : {idx + 1} of {len(self._anim_frames)}",
            f"Timing     : {timing_str}",
            f"LEDs       : {counts[0]} STATIC  |  {counts[1]} SNAP  |  {counts[2]} LINEAR",
            "",
        ]
        for name, flat in [("Eye   (p0, LED 0)", 0),
                            ("Mouth (p2, LED 128)", 128),
                            ("Nose  (p6, LED 384)", 384)]:
            r, g, b, sm, param = leds[flat]
            sm_str = {0: 'STATIC', 1: 'SNAP', 2: 'LINEAR'}.get(sm, '?')
            lines.append(f"  {name}  RGB({r:3},{g:3},{b:3})  {sm_str}  param={param}")

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
            idx = max(1, min(len(self._anim_frames), int(self._chosen_idx.get()))) - 1
        except (ValueError, tk.TclError):
            messagebox.showerror("Error", "Invalid frame index.")
            return

        out_path = self.out_var.get().strip()
        if not out_path:
            messagebox.showerror("Error", "Please specify an output path.")
            return

        frame  = self._anim_frames[idx]
        total  = len(self._anim_frames)
        self._generate_h(frame, idx, total, self._source_name, out_path)
        self.status_label.config(
            text=f"✓  Written → {out_path}   (frame {idx+1}/{total})")

    def _generate_h(self, frame, frame_idx, total_frames, source_name, out_path):
        dur    = frame['duration_ms']
        timing = frame['timing_mode']
        leds   = frame['leds']
        timing_str = "SOUND_TRIGGERED" if timing == 1 else f"TIMED ({dur}ms)"

        lines = [
            '#pragma once',
            '#include <stdint.h>',
            '',
            '// ── Fallback animation ─────────────────────────────────────────────────────',
            f'// Source : {source_name}',
            f'// Frame  : {frame_idx + 1} of {total_frames}',
            f'// Timing : {timing_str}',
            '//',
            '// sound_mode : 0=STATIC  1=SNAP  2=LINEAR',
            '// param      : SNAP   → threshold 0-255',
            '//              LINEAR → high nibble=m (0-15)  low nibble=b (0-15)',
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
            '    LEDEntry leds[896];',
            '};',
            '',
            f'static const uint16_t FALLBACK_DURATION_MS = {dur};',
            f'static const uint8_t  FALLBACK_TIMING_MODE = {timing};',
            '',
            'static const LEDEntry FALLBACK_LEDS[896] PROGMEM = {',
        ]

        for i, led in enumerate(leds):
            r, g, b, sm, p = led
            comma = ',' if i < TOTAL_LEDS - 1 else ' '
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

        with open(out_path, 'w') as fh:
            fh.write('\n'.join(lines) + '\n')


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Protogen Tool")
        root.configure(bg='#0d0d1a')

        # Style notebook tabs
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

        self.painter  = PainterTab(nb)
        self.sim      = SimulatorTab(nb, self.painter.get_frames_data)
        self.exporter = ExportTab(nb, self.painter.get_frames_data)

        nb.add(self.painter.frame,  text="  🎨  Painter  ")
        nb.add(self.sim.frame,      text="  ▶   Simulator  ")
        nb.add(self.exporter.frame, text="  📄  Export .h  ")

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._mic_refresh()

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