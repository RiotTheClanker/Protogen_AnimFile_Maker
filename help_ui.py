"""
help_ui.py  —  Onboarding aids for protogen_tool.py

  • Tooltip      hover text for any widget (also shown in the status bar)
  • StatusBar    one line at the bottom of the window for hints
  • HelpOverlay  the help guide, drawn over the main window (F1)
  • Settings     a tiny JSON file that remembers "show the guide at startup"

The help text lives here, in HELP_TOPICS, so the tool and the guide stay in
one place.  It uses a small markup:  "## " starts a heading, "• " a bullet,
and **text** is bold.
"""

import json, os, sys
import tkinter as tk

BG      = '#0d0d1a'
CARD_BG = '#1a1a2e'
PANEL   = '#16213e'
ACCENT  = '#e94560'
TEXT    = '#e8e8f0'
MUTED   = '#9a9ab0'
KEY_BG  = '#0f3460'


# ── Settings ──────────────────────────────────────────────────────────────────
def _settings_path():
    if sys.platform.startswith('win'):
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'ProtogenTool', 'settings.json')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Application Support/ProtogenTool/settings.json')
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'protogen_tool', 'settings.json')


class Settings:
    """Remembers a few preferences between runs.  Never raises: a missing or
    unwritable file just means the defaults are used."""
    DEFAULTS = {'show_guide_at_start': False, 'guide_seen': False}

    def __init__(self, path=None):
        self.path = path or _settings_path()
        self.data = dict(self.DEFAULTS)
        try:
            with open(self.path, encoding='utf-8') as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'w', encoding='utf-8') as fh:
                json.dump(self.data, fh, indent=2)
        except OSError:
            pass


# ── Status bar ────────────────────────────────────────────────────────────────
class StatusBar(tk.Frame):
    """A hint line at the bottom of the window.  Tooltips write into it while
    the pointer is over a widget; otherwise it shows the current tab's hint."""
    def __init__(self, parent):
        super().__init__(parent, bg=PANEL)
        self._default = ''
        self._label = tk.Label(self, text='', bg=PANEL, fg=MUTED, anchor='w',
                               font=('Helvetica', 9), padx=10, pady=3)
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(self, text='F1 = help', bg=PANEL, fg=MUTED,
                 font=('Helvetica', 9), padx=10).pack(side=tk.RIGHT)

    def set_default(self, text):
        self._default = text
        self._label.config(text=text, fg=MUTED)

    def show(self, text):
        self._label.config(text=text, fg=TEXT)

    def clear(self):
        self._label.config(text=self._default, fg=MUTED)


# ── Tooltips ──────────────────────────────────────────────────────────────────
class Tooltip:
    """Shows `text` in a small box after the pointer rests on `widget`, and in
    the status bar straight away."""
    DELAY_MS = 600
    status = None              # the app's StatusBar, set once at startup

    def __init__(self, widget, text):
        self.widget = widget
        self.text   = text
        self._after = None
        self._tip   = None
        widget.bind('<Enter>', self._enter, add='+')
        widget.bind('<Leave>', self._leave, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _enter(self, _event=None):
        if Tooltip.status is not None:
            Tooltip.status.show(self.text)
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self._show)

    def _leave(self, _event=None):
        if Tooltip.status is not None:
            Tooltip.status.clear()
        self._hide()

    def _cancel(self):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        self._after = None
        if self._tip is not None or not self.widget.winfo_ismapped():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tk.Label(tw, text=self.text, bg='#fff8dc', fg='#222', justify=tk.LEFT,
                 wraplength=280, relief=tk.SOLID, borderwidth=1,
                 font=('Helvetica', 9), padx=6, pady=4).pack()
        # Keep the box on screen
        tw.update_idletasks()
        sw = tw.winfo_screenwidth()
        sh = tw.winfo_screenheight()
        x = min(x, sw - tw.winfo_reqwidth() - 4)
        if y + tw.winfo_reqheight() > sh - 4:
            y = self.widget.winfo_rooty() - tw.winfo_reqheight() - 4
        tw.wm_geometry(f"+{max(0, x)}+{max(0, y)}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def tip(widget, text):
    """Attach a tooltip and return the widget, so it can wrap a constructor."""
    Tooltip(widget, text)
    return widget


# ── Keyboard shortcuts (shown in the guide; bound in protogen_tool.py) ────────
SHORTCUTS = [
    ("Anywhere", [
        ("F1", "Open or close this guide"),
        ("Ctrl+1 / 2 / 3", "Go to the Painter / Simulator / Export tab"),
    ]),
    ("Painter", [
        ("Click / drag", "Paint LEDs with the brush"),
        ("Right-click / drag", "Erase LEDs"),
        ("Ctrl+Z", "Undo"),
        ("Ctrl+N", "Add a new blank frame"),
        ("Ctrl+D", "Duplicate this frame (a good start for the next one)"),
        ("← / →", "Previous / next frame"),
        ("E", "Eraser on or off"),
        ("M", "Mirror to other side on or off"),
        ("C", "Pick a colour"),
        ("1 – 8", "Pick a quick colour swatch"),
        ("Ctrl+O", "Open a .anim file"),
        ("Ctrl+S", "Save as a .anim file"),
    ]),
    ("Simulator", [
        ("Space", "Play / stop"),
        ("← / →", "Previous / next frame"),
        ("P", "Load the Painter's frames"),
    ]),
]


# ── Help text ─────────────────────────────────────────────────────────────────
HELP_TOPICS = [
    ("Getting started", """\
## Make your first animation in 5 steps
**1. Pick your display.**  Use the **Display** menu at the top. It has to match the helmet the firmware is set up for (Mk2 is the default). If you have a different display, click **Open display.json…**.
**2. Paint a face.**  On the **🎨 Painter** tab, pick a colour on the right, then click or drag on the face. **Right-click** erases. **Mirror to other side** is on, so the other half of the face is painted for you.
**3. Add more frames.**  Click **Duplicate** to copy the frame you're on, then change something (open the eyes a bit, move the mouth…). Each frame is one picture of the animation. Use **◀ Prev / Next ▶** to move between them.
**4. Watch it play.**  Go to the **▶ Simulator** tab and click **Preview Painter Frames**, then **▶ Auto Play**.
**5. Save it and put it on the helmet.**  Back on the Painter, click **Save .anim** (Ctrl+S). Copy the file to the root of the helmet's SD card. Name it **start.anim** to play it when the helmet turns on, or pick it over Bluetooth with the **list** and **load** commands.

## Good to know
• Hover over any button to see what it does. The bar at the bottom of the window shows the same hint.
• **Ctrl+Z** undoes your last stroke, fill or clear.
• The brush settings (colour, sound reaction) are used for the LEDs you paint **next**. They don't change LEDs you already painted. To change those, paint over them.
• The timing settings belong to the **frame you're on**. Every frame has its own.
• You can open this guide again any time with **F1** or the **? Help** button.
"""),
    ("Painting", """\
## The face
The face is drawn the way it's laid out on the helmet. Each square is one LED. Coloured boxes mark the regions (eyes, mouth, nose) and their names. When you move the pointer over the face, the bar at the bottom says which panel and pixel you're on.

## Brush
• **Colour**: click a swatch, or **Pick Color…** for any colour.
• **Eraser**: paints LEDs black (off). Right-click erases too, without turning the eraser on.
• **Mirror to other side**: whatever you paint on one side is painted on the matching spot of the other side.
• **Flip left↔right when mirroring**: makes the other side a mirror image, like a real face. Leave this on for Mk2 and Mk3. If your right side comes out backwards on the helmet, turn it off.

## Fill region
Fills a whole eye, mouth or nose with the brush at once. **All** fills everything. **Copy left → right** copies the whole left side onto the right side.

## Frames
• **+ New** adds a blank frame at the end.
• **Duplicate** copies the frame you're on. This is the easiest way to animate: copy, then change a little.
• **Delete** removes the frame you're on.
• **Clear Frame** turns every LED in this frame off.
"""),
    ("Sound reaction", """\
## Make LEDs react to your voice
The helmet has a microphone. Each LED can react to how loud it is. Choose the reaction **before** you paint; the LEDs you paint get the brush's reaction.

• **Static (always on)**: the LED just shows its colour. Use this for most of the face.
• **Snap (on when loud)**: the LED is off when it's quiet and turns on when the volume reaches the **threshold**. A low threshold (like 60) turns on with quiet sounds; a high one (like 200) needs shouting. Good for a mouth that opens when you talk.
• **Linear (brighter when louder)**: the LED fades in with the volume. **b** is how bright it is when it's silent (0 = off, 15 = full). **m** is how fast it gets brighter: m = 1 fades in slowly over the whole volume range, and a high m like 12 is fully bright as soon as there's some sound.

## Try it
Paint some Snap LEDs, then open the **▶ Simulator**, click **Preview Painter Frames**, and move the **volume** slider (or turn on the mic). You'll see exactly what the helmet would do.
"""),
    ("Frame timing", """\
## How long each frame stays on
Every frame has its own timing. The settings show the frame you're on.

• **Timed**: the frame is shown for the time you set, then the next frame comes. 500 ms is half a second. A blink is about 100–150 ms per frame.
• **Sound triggered**: the frame stays until there's a loud sound (the volume goes past 128), then the next frame comes. The time you set is the **shortest** it stays on, so one sound can't skip through many frames.

After the last frame, the animation starts again from frame 1.

## Tip
To make a face blink now and then: frame 1 with open eyes, Timed, 3000 ms. Frame 2 with half-closed eyes, 80 ms. Frame 3 with closed eyes, 120 ms. Frame 4 like frame 2, 80 ms.
"""),
    ("Simulator", """\
## Check your animation before you copy it to the helmet
• **Preview Painter Frames** plays what you've painted, without saving first.
• **Open .anim** plays any saved file.
• **▶ Auto Play** runs it with the same timing and sound rules as the helmet. **◀ Prev / Next ▶** step one frame.
• **Manual volume** pretends there's sound, so you can see Snap and Linear LEDs and sound-triggered frames. **🎤 Use Mic** uses your computer's microphone instead (it needs pyaudio).
"""),
    ("Export .h", """\
## You probably don't need this tab
To put an animation on the helmet, **save a .anim** and copy it to the SD card. That's all.

The Export tab is for people who build the firmware themselves. It turns **one frame** into **fallback_anim.h**, the picture that's built into the firmware and shown when there's no SD card or no start.anim. Copy the file into **ProtoFace/src/** and rebuild the firmware.
"""),
    ("Displays", """\
## Which display am I using?
The tool needs to know how your LEDs are laid out. Pick it in the **Display** menu at the top:
• **Mk2**: 11 panels (eyes, 3 mouth panels and a nose on the left; eyes and 3 mouth panels on the right).
• **Mk3**: 14 panels (eyes, 4 mouth panels and a nose on each side).
• Anything else (a HUB75 panel, MAX7219 matrices, your own LED board): click **Open display.json…** and pick the file that describes it. The same file goes on the SD card for the firmware.

Pick the display **before** you start. If you switch later, your drawing is moved over panel by panel (eyes to eyes, mouth to mouth), and panels the new display doesn't have are dropped.

When you open a .anim made for another display, the tool offers to switch to it.
"""),
    ("Shortcuts", None),     # built from SHORTCUTS
    ("Problems?", """\
## The right side of the face is black on the helmet
Turn on **Mirror to other side** before painting, or click **Copy left → right** in Fill region.

## The right side is backwards
Turn **Flip left↔right when mirroring** off (or on), then repaint or use **Copy left → right** again.

## My Snap or Linear LEDs never light up in the Simulator
Move the **Manual volume** slider up. With no sound they're meant to be off (Snap) or dim (Linear).

## The animation never moves on
Your frames are probably **Sound triggered**, so they wait for a loud sound. Raise the volume in the Simulator, or switch the frames to **Timed**.

## The helmet shows "NO SD" or warning triangles
That's the built-in fallback face. The helmet didn't find an SD card, or there's no **start.anim** on it. Check the card is FAT32 and the file is in its root folder (not in a folder).

## The helmet won't play my file, or the face looks scrambled
The display in the tool doesn't match the one the firmware is set up for. Pick the right one in the **Display** menu (switching keeps your drawing), then save the file again.
"""),
]


# ── Help overlay ──────────────────────────────────────────────────────────────
class HelpOverlay:
    """
    The guide, drawn on top of the whole window: topics on the left, text on
    the right.  F1, Esc or the Close button hide it.
    """
    def __init__(self, root, settings):
        self.root     = root
        self.settings = settings
        self.visible  = False
        self._topic   = 0

        self.frame = tk.Frame(root, bg=BG)
        card = tk.Frame(self.frame, bg=CARD_BG, highlightthickness=2,
                        highlightbackground=ACCENT)
        card.place(relx=0.5, rely=0.5, anchor='center', relwidth=0.9, relheight=0.9)

        # Header
        head = tk.Frame(card, bg=CARD_BG)
        head.pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Label(head, text="Protogen Tool guide", bg=CARD_BG, fg=ACCENT,
                 font=('Helvetica', 16, 'bold')).pack(side=tk.LEFT)
        tk.Button(head, text="✕ Close", command=self.hide, bg=PANEL, fg='white',
                  padx=10).pack(side=tk.RIGHT)

        # Footer
        foot = tk.Frame(card, bg=CARD_BG)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(4, 12))
        self._show_at_start = tk.BooleanVar(value=bool(settings.get('show_guide_at_start')))
        tk.Checkbutton(foot, text="Show this guide every time the tool starts",
                       variable=self._show_at_start, command=self._save_pref,
                       bg=CARD_BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=CARD_BG, activeforeground=TEXT).pack(side=tk.LEFT)
        tk.Label(foot, text="F1 opens and closes this guide · Esc closes it",
                 bg=CARD_BG, fg=MUTED, font=('Helvetica', 9)).pack(side=tk.RIGHT)
        self._next_btn = tk.Button(foot, text="Next topic ▶", command=self._next,
                                   bg=KEY_BG, fg='white', padx=10)
        self._next_btn.pack(side=tk.RIGHT, padx=12)

        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        # Topic list
        nav = tk.Frame(body, bg=CARD_BG)
        nav.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        self._nav_buttons = []
        for i, (title, _text) in enumerate(HELP_TOPICS):
            b = tk.Button(nav, text=title, anchor='w', relief=tk.FLAT, padx=10, pady=4,
                          width=16, bg=PANEL, fg='white',
                          activebackground=KEY_BG, activeforeground='white',
                          command=lambda i=i: self.show_topic(i))
            b.pack(fill=tk.X, pady=1)
            self._nav_buttons.append(b)

        # Text
        text_wrap = tk.Frame(body, bg=CARD_BG)
        text_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar = tk.Scrollbar(text_wrap, orient=tk.VERTICAL)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text = tk.Text(text_wrap, bg=BG, fg=TEXT, wrap=tk.WORD, relief=tk.FLAT,
                            padx=16, pady=12, font=('Helvetica', 11),
                            spacing1=2, spacing3=4, yscrollcommand=bar.set,
                            cursor='arrow', highlightthickness=0)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.config(command=self.text.yview)
        self.text.tag_configure('h', font=('Helvetica', 13, 'bold'), foreground=ACCENT,
                                spacing1=12, spacing3=6)
        self.text.tag_configure('b', font=('Helvetica', 11, 'bold'))
        self.text.tag_configure('bullet', lmargin1=8, lmargin2=24)
        self.text.tag_configure('key', font=('Courier', 10, 'bold'), background=KEY_BG,
                                foreground='white')
        self.text.tag_configure('keyrow', tabs=('190',), lmargin1=8)

    # ── Show / hide ───────────────────────────────────────────────────────────
    def show(self, topic=None):
        if topic is not None:
            self.show_topic(topic)
        elif not self.text.get('1.0', 'end-1c'):
            self.show_topic(0)
        self.frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.frame.lift()
        self.frame.focus_set()
        self.visible = True

    def hide(self):
        self.frame.place_forget()
        self.visible = False

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def _save_pref(self):
        self.settings.set('show_guide_at_start', bool(self._show_at_start.get()))

    def _next(self):
        self.show_topic((self._topic + 1) % len(HELP_TOPICS))

    # ── Topics ────────────────────────────────────────────────────────────────
    def topic_index(self, title):
        return next(i for i, (t, _x) in enumerate(HELP_TOPICS) if t == title)

    def show_topic(self, i):
        self._topic = i
        for k, b in enumerate(self._nav_buttons):
            b.config(bg=KEY_BG if k == i else PANEL)
        title, body = HELP_TOPICS[i]
        self.text.config(state=tk.NORMAL)
        self.text.delete('1.0', tk.END)
        if body is None:
            self._render_shortcuts()
        else:
            self._render(body)
        self.text.config(state=tk.DISABLED)
        self.text.yview_moveto(0)
        nxt = HELP_TOPICS[(i + 1) % len(HELP_TOPICS)][0]
        self._next_btn.config(text=f"Next: {nxt} ▶")

    def _insert_inline(self, line, tags=()):
        """Insert one line, making **bold** parts bold."""
        for k, part in enumerate(line.split('**')):
            if part:
                self.text.insert(tk.END, part, tags + (('b',) if k % 2 else ()))

    def _render(self, body):
        for line in body.splitlines():
            if line.startswith('## '):
                self.text.insert(tk.END, line[3:] + '\n', ('h',))
            elif line.startswith('• '):
                self._insert_inline(line, ('bullet',))
                self.text.insert(tk.END, '\n')
            else:
                self._insert_inline(line)
                self.text.insert(tk.END, '\n')

    def _render_shortcuts(self):
        for group, rows in SHORTCUTS:
            self.text.insert(tk.END, group + '\n', ('h',))
            for keys, what in rows:
                self.text.insert(tk.END, f" {keys} ", ('key', 'keyrow'))
                self.text.insert(tk.END, '\t' + what + '\n', ('keyrow',))
        self.text.insert(tk.END, '\n')
        if sys.platform == 'darwin':
            self._insert_inline("On a Mac, use **⌘ Command** where this says Ctrl.\n")
        self._insert_inline("Letter shortcuts don't work while you're typing in a box, "
                            "or while a slider is selected. Click the face first.")
        self.text.insert(tk.END, '\n')
