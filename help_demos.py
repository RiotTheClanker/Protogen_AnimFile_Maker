"""
help_demos.py  —  small live demos embedded in the help guide

  PixelDemo   one 8×8 panel; hover an LED to see what it stores
  SoundDemo   Static, Snap and Linear LEDs side by side with a volume control
  FrameDemo   a three-frame mouth animation, Timed or Sound triggered

They use the same sound and timing rules as the firmware (anim_io), so what
they show is what the helmet does.  Each demo has stop(), which cancels its
timer; the guide calls it before destroying the demo.
"""

import math, random, time
import tkinter as tk

from anim_io import (apply_sound, pack_linear, SOUND_STATIC, SOUND_SNAP, SOUND_LINEAR,
                     SOUND_TRIGGER_LEVEL, SOUND_TRIGGER_RELEASE)

BG     = '#0d0d1a'
CARD   = '#16213e'
TEXT   = '#e8e8f0'
MUTED  = '#9a9ab0'
ACCENT = '#e94560'
OFF    = '#111122'
CYAN   = (0, 255, 255)
PINK   = (255, 60, 160)

TICK_MS = 40


def hex_color(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def mode_text(led):
    sm, param = led[3], led[4]
    if sm == SOUND_SNAP:
        return f"Snap: on when the volume is {param} or more"
    if sm == SOUND_LINEAR:
        return f"Linear: m {param >> 4}, b {param & 15}, brighter when louder"
    return "Static: always on"


class _Demo(tk.Frame):
    """Base: a framed box with a repeating timer."""
    def __init__(self, parent):
        super().__init__(parent, bg=CARD, padx=12, pady=10)
        self._after = None

    def _start_timer(self):
        self._after = self.after(TICK_MS, self._timer)

    def _timer(self):
        self._after = None
        self.tick()
        self._start_timer()

    def tick(self):
        pass

    def stop(self):
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None


# ── Volume control shared by the sound and frame demos ────────────────────────
class VolumeControl(tk.Frame):
    """A volume slider, a "Pretend to talk" switch that moves it like a
    voice, and a bar with the sound-trigger level marked."""
    W = 300

    def __init__(self, parent, mark=None):
        super().__init__(parent, bg=CARD)
        self.volume = tk.IntVar(value=0)
        self.talking = tk.BooleanVar(value=False)
        self._mark = mark if mark is not None else SOUND_TRIGGER_LEVEL
        self._phase = 0.0
        self._level = 0.0

        row = tk.Frame(self, bg=CARD)
        row.pack(anchor='w')
        tk.Label(row, text="Volume:", bg=CARD, fg=TEXT).pack(side=tk.LEFT)
        slider = tk.Scale(row, from_=0, to=255, orient=tk.HORIZONTAL, variable=self.volume,
                          length=200, bg=CARD, fg=TEXT, troughcolor=BG, highlightthickness=0)
        slider.pack(side=tk.LEFT, padx=6)
        slider.bind('<ButtonPress-1>', lambda e: self.talking.set(False), add='+')
        tk.Checkbutton(row, text="🗣 Pretend to talk", variable=self.talking,
                       bg=CARD, fg=TEXT, selectcolor=BG, activebackground=CARD,
                       activeforeground=TEXT).pack(side=tk.LEFT, padx=6)

        self.bar = tk.Canvas(self, width=self.W, height=22, bg=BG, highlightthickness=0)
        self.bar.pack(anchor='w', pady=(4, 0))
        self._fill = self.bar.create_rectangle(0, 4, 0, 16, fill='#00e676', outline='')
        self._tick = self.bar.create_line(0, 0, 0, 22, fill=ACCENT, width=2)
        self._tick_label = self.bar.create_text(0, 11, anchor='w', fill=ACCENT,
                                                font=('Helvetica', 8))
        self.set_mark(self._mark)

    def set_mark(self, level, label=None):
        self._mark = level
        x = int(self.W * level / 255)
        self.bar.coords(self._tick, x, 0, x, 22)
        self.bar.coords(self._tick_label, x + 4, 11)
        self.bar.itemconfig(self._tick_label, text=label or str(level))

    def get(self):
        return self.volume.get()

    def tick(self):
        """Call every TICK_MS: moves the volume while "talking"."""
        if self.talking.get():
            # Syllables: bursts of sound with short gaps, like speech
            self._phase += TICK_MS / 1000.0
            target = 0
            if math.sin(self._phase * 7.0) > -0.2:
                target = 150 + 90 * math.sin(self._phase * 23.0) + random.uniform(-30, 30)
            self._level += (target - self._level) * 0.5
            self.volume.set(int(max(0, min(255, self._level))))
        v = self.get()
        self.bar.coords(self._fill, 0, 4, int(self.W * v / 255), 16)
        self.bar.itemconfig(self._fill, fill='#00e676' if v < self._mark else '#ffb74d')


# ── Pixels ────────────────────────────────────────────────────────────────────
class PixelDemo(_Demo):
    CELL = 26
    # An eye: '#' Static cyan, 's' Snap pink (only lit when loud), '.' off
    ART = ["..####..",
           ".######.",
           "########",
           "###..###",
           "###..###",
           "########",
           ".######.",
           "s.s..s.s"]

    def __init__(self, parent):
        super().__init__(parent)
        snap = [*PINK, SOUND_SNAP, 120]
        self.leds = {}
        for y, row in enumerate(self.ART):
            for x, ch in enumerate(row):
                self.leds[(x, y)] = ([*CYAN, SOUND_STATIC, 0] if ch == '#' else
                                     snap if ch == 's' else [0, 0, 0, SOUND_STATIC, 0])
        n = 8 * self.CELL
        left = tk.Frame(self, bg=CARD)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="One 8 × 8 panel = 64 LEDs", bg=CARD, fg=MUTED,
                 font=('Helvetica', 9)).pack(anchor='w')
        self.canvas = tk.Canvas(left, width=n + 2, height=n + 2, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()
        self._items = {}
        for (x, y), led in self.leds.items():
            x1, y1 = 1 + x * self.CELL, 1 + y * self.CELL
            it = self.canvas.create_rectangle(x1, y1, x1 + self.CELL - 2, y1 + self.CELL - 2,
                                              fill=hex_color(led[:3]) if any(led[:3]) else OFF,
                                              outline='#2a2a4a')
            self._items[it] = (x, y)
            if led[3] == SOUND_SNAP:
                self.canvas.create_text(x1 + self.CELL // 2 - 1, y1 + self.CELL // 2 - 1,
                                        text='S', fill='white', font=('Helvetica', 9, 'bold'),
                                        state=tk.DISABLED)
        self._hl = self.canvas.create_rectangle(0, 0, 0, 0, outline='white', width=2)
        self.canvas.bind('<Motion>', self._hover)

        right = tk.Frame(self, bg=CARD)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))
        tk.Label(right, text="Point at an LED:", bg=CARD, fg=MUTED).pack(anchor='w')
        self.info = tk.Label(right, text="", bg=CARD, fg=TEXT, justify=tk.LEFT,
                             wraplength=300, font=('Helvetica', 11))
        self.info.pack(anchor='w', pady=(2, 10))
        tk.Label(right, bg=CARD, fg=MUTED, justify=tk.LEFT, wraplength=300,
                 text="Every LED remembers two things:\n"
                      "  1. its colour (black = off)\n"
                      "  2. how it reacts to sound\n\n"
                      "The pink LEDs marked S are Snap LEDs: on the helmet they "
                      "only light up while you talk. In the Painter they're "
                      "marked S (Snap) or L (Linear) the same way.").pack(anchor='w')
        self._show((3, 1))

    def _hover(self, event):
        x, y = event.x // self.CELL, event.y // self.CELL
        if (x, y) in self.leds:
            self._show((x, y))

    def _show(self, xy):
        x, y = xy
        led = self.leds[xy]
        x1, y1 = 1 + x * self.CELL, 1 + y * self.CELL
        self.canvas.coords(self._hl, x1 - 1, y1 - 1, x1 + self.CELL - 1, y1 + self.CELL - 1)
        if any(led[:3]):
            colour = f"colour {hex_color(led[:3])}  (red {led[0]}, green {led[1]}, blue {led[2]})"
            self.info.config(text=f"LED x {x}, y {y}\n{colour}\n{mode_text(led)}")
        else:
            self.info.config(text=f"LED x {x}, y {y}\nblack: this LED stays off")


# ── Sound reaction ────────────────────────────────────────────────────────────
class SoundDemo(_Demo):
    SIZE = 64

    def __init__(self, parent):
        super().__init__(parent)
        tk.Label(self, text="Three LEDs, all painted cyan, each with a different sound "
                            "reaction. Move the volume or tick Pretend to talk.",
                 bg=CARD, fg=MUTED, wraplength=560, justify=tk.LEFT).pack(anchor='w')
        self.vol = VolumeControl(self)
        self.snap_at = tk.IntVar(value=SOUND_TRIGGER_LEVEL)
        self.lin_m = tk.IntVar(value=1)
        self.lin_b = tk.IntVar(value=3)

        row = tk.Frame(self, bg=CARD)
        row.pack(anchor='w', pady=(8, 6))
        self._cols = []
        for title, settings in [("Static", None),
                                ("Snap", (self.snap_at, "turn on at", 0, 255)),
                                ("Linear", None)]:
            col = tk.Frame(row, bg=CARD, width=190)
            col.pack(side=tk.LEFT, anchor='n', padx=(0, 16))
            tk.Label(col, text=title, bg=CARD, fg=ACCENT,
                     font=('Helvetica', 11, 'bold')).pack()
            c = tk.Canvas(col, width=self.SIZE, height=self.SIZE, bg=CARD,
                          highlightthickness=0)
            c.pack(pady=4)
            dot = c.create_oval(4, 4, self.SIZE - 4, self.SIZE - 4, fill=OFF,
                                outline='#2a2a4a', width=2)
            status = tk.Label(col, text="", bg=CARD, fg=TEXT, width=24,
                              font=('Helvetica', 9))
            status.pack()
            self._cols.append((c, dot, status, col))

        def small_slider(col, label, var, lo, hi):
            r = tk.Frame(col, bg=CARD)
            r.pack()
            tk.Label(r, text=label, bg=CARD, fg=MUTED, font=('Helvetica', 8)).pack(side=tk.LEFT)
            tk.Scale(r, from_=lo, to=hi, orient=tk.HORIZONTAL, variable=var, length=110,
                     bg=CARD, fg=TEXT, troughcolor=BG, highlightthickness=0,
                     font=('Helvetica', 8)).pack(side=tk.LEFT)

        tk.Label(self._cols[0][3], text="Always shows its colour.", bg=CARD, fg=MUTED,
                 font=('Helvetica', 8)).pack()
        small_slider(self._cols[1][3], "threshold", self.snap_at, 0, 255)
        small_slider(self._cols[2][3], "m (speed)", self.lin_m, 0, 15)
        small_slider(self._cols[2][3], "b (when silent)", self.lin_b, 0, 15)
        self.vol.pack(anchor='w', pady=(4, 0))
        self.tick()
        self._start_timer()

    def tick(self):
        self.vol.tick()
        v = self.vol.get()
        self.vol.set_mark(self.snap_at.get(), f"Snap threshold {self.snap_at.get()}")
        leds = [[*CYAN, SOUND_STATIC, 0],
                [*CYAN, SOUND_SNAP, self.snap_at.get()],
                [*CYAN, SOUND_LINEAR, pack_linear(self.lin_m.get(), self.lin_b.get())]]
        for (c, dot, status, _col), led in zip(self._cols, leds):
            rgb = apply_sound(led, v)
            c.itemconfig(dot, fill=hex_color(rgb) if any(rgb) else OFF)
            if led[3] == SOUND_STATIC:
                text = "on"
            elif led[3] == SOUND_SNAP:
                text = (f"on: {v} ≥ {led[4]}" if v >= led[4]
                        else f"off: {v} is below {led[4]}")
            else:
                text = f"brightness {round(100 * rgb[1] / 255)}%"
            status.config(text=text)


# ── Frames ────────────────────────────────────────────────────────────────────
class FrameDemo(_Demo):
    W, H = 16, 6
    BIG, SMALL = 14, 5
    # A mouth opening: closed, half open, open
    SHAPES = [["................",
               "................",
               "################",
               "................",
               "................",
               "................"],
              ["................",
               ".##############.",
               "#..............#",
               ".##############.",
               "................",
               "................"],
              ["..############..",
               ".#............#.",
               "#..............#",
               "#..............#",
               ".#............#.",
               "..############.."]]
    DURATIONS = [400, 150, 250]

    def __init__(self, parent):
        super().__init__(parent)
        self.mode = tk.StringVar(value='timed')
        self.current = 0
        self._start = time.monotonic()
        self._armed = True

        tk.Label(self, text="An animation is a list of frames, like a flip book. "
                            "The helmet shows one frame at a time, then moves to the "
                            "next, and starts again after the last one.",
                 bg=CARD, fg=MUTED, wraplength=560, justify=tk.LEFT).pack(anchor='w')

        thumbs = tk.Frame(self, bg=CARD)
        thumbs.pack(anchor='w', pady=(8, 4))
        self._thumbs = []
        for i, shape in enumerate(self.SHAPES):
            box = tk.Frame(thumbs, bg=CARD, highlightthickness=2, highlightbackground=CARD)
            box.pack(side=tk.LEFT, padx=(0, 8))
            c = tk.Canvas(box, width=self.W * self.SMALL, height=self.H * self.SMALL,
                          bg=BG, highlightthickness=0)
            c.pack(padx=4, pady=(4, 0))
            self._paint(c, shape, self.SMALL)
            cap = tk.Label(box, text="", bg=CARD, fg=TEXT, font=('Helvetica', 8))
            cap.pack()
            self._thumbs.append((box, cap))
            if i < len(self.SHAPES) - 1:
                tk.Label(thumbs, text="→", bg=CARD, fg=MUTED,
                         font=('Helvetica', 14)).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(thumbs, text="↺ back to 1", bg=CARD, fg=MUTED,
                 font=('Helvetica', 9)).pack(side=tk.LEFT)

        main = tk.Frame(self, bg=CARD)
        main.pack(anchor='w', pady=(6, 0))
        self.big = tk.Canvas(main, width=self.W * self.BIG, height=self.H * self.BIG,
                             bg=BG, highlightthickness=0)
        self.big.pack(side=tk.LEFT)
        side = tk.Frame(main, bg=CARD)
        side.pack(side=tk.LEFT, anchor='n', padx=(16, 0))
        tk.Label(side, text="Frame timing:", bg=CARD, fg=TEXT).pack(anchor='w')
        for text, val in [("Timed: each frame for its own time", 'timed'),
                          ("Sound triggered: next frame on each loud sound", 'sound')]:
            tk.Radiobutton(side, text=text, variable=self.mode, value=val, bg=CARD,
                           fg=TEXT, selectcolor=BG, activebackground=CARD,
                           activeforeground=TEXT, command=self._restart).pack(anchor='w')
        self.status = tk.Label(side, text="", bg=CARD, fg='#4fc3f7', justify=tk.LEFT,
                               wraplength=300, font=('Helvetica', 10))
        self.status.pack(anchor='w', pady=(8, 0))

        self.vol = VolumeControl(self)
        self.vol.pack(anchor='w', pady=(8, 0))
        self._big_items = self._paint(self.big, self.SHAPES[0], self.BIG)
        self._show_frame()
        self._start_timer()

    def _paint(self, canvas, shape, cell):
        items = {}
        for y, row in enumerate(shape):
            for x, ch in enumerate(row):
                items[(x, y)] = canvas.create_rectangle(
                    x * cell, y * cell, (x + 1) * cell - 1, (y + 1) * cell - 1,
                    fill=hex_color(CYAN) if ch == '#' else OFF,
                    outline=BG if cell > 6 else '')
        return items

    def _restart(self):
        self.current = 0
        self._start = time.monotonic()
        self._armed = self.vol.get() < SOUND_TRIGGER_RELEASE
        self._show_frame()

    def _show_frame(self):
        shape = self.SHAPES[self.current]
        for (x, y), it in self._big_items.items():
            self.big.itemconfig(it, fill=hex_color(CYAN) if shape[y][x] == '#' else OFF)
        sound = self.mode.get() == 'sound'
        for i, (box, cap) in enumerate(self._thumbs):
            box.config(highlightbackground=ACCENT if i == self.current else CARD)
            cap.config(text=f"Frame {i + 1}" + ("" if sound else f" · {self.DURATIONS[i]} ms"))

    def tick(self):
        self.vol.tick()
        v = self.vol.get()
        elapsed = (time.monotonic() - self._start) * 1000
        n = len(self.SHAPES)
        if self.mode.get() == 'timed':
            dur = self.DURATIONS[self.current]
            advance = elapsed >= dur
            left = max(0, int(dur - elapsed))
            self.status.config(text=f"Showing frame {self.current + 1} of {n}.\n"
                                    f"Next frame in {left} ms. The volume does nothing here.")
            self.vol.set_mark(SOUND_TRIGGER_LEVEL, "(not used)")
        else:
            self.vol.set_mark(SOUND_TRIGGER_LEVEL, f"trigger {SOUND_TRIGGER_LEVEL}")
            if v < SOUND_TRIGGER_RELEASE:
                self._armed = True
            advance = self._armed and v >= SOUND_TRIGGER_LEVEL and elapsed >= 100
            wait = ("Waiting for a loud sound (volume past "
                    f"{SOUND_TRIGGER_LEVEL})." if self._armed else
                    f"Waiting for it to get quiet first (below {SOUND_TRIGGER_RELEASE}), "
                    "so one sound only moves one frame.")
            self.status.config(text=f"Showing frame {self.current + 1} of {n}.\n{wait}")
        if advance:
            self.current = (self.current + 1) % n
            self._start = time.monotonic()
            if self.mode.get() == 'sound':
                self._armed = False
            self._show_frame()


DEMOS = {'pixels': PixelDemo, 'sound': SoundDemo, 'frames': FrameDemo}
