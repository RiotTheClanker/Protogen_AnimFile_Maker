# Protogen_AnimFile_Maker
A tool for making, testing and converting `.anim` files for the
[ProtoFace](https://github.com/RiotTheClanker/ProtoFace) visor firmware.

## Running

```bash
pip install numpy pyaudio   # pyaudio is optional (live mic in the Simulator)
python protogen_tool.py
```

On Linux you may also need `sudo apt install python3-tk portaudio19-dev`.
Prebuilt Windows / macOS / Linux executables are on the
[Releases](https://github.com/RiotTheClanker/Protogen_AnimFile_Maker/releases) page.

### Releasing

Add notes as `docs/releases/<version>.md`, then either push a tag (`git tag V4 && git push origin V4`)
or open **Actions → Release → Run workflow** on `main` and enter the version. The workflow
builds all three platforms and publishes `Windows_protogen_tool.exe`, `Mac_protogen_tool` and
`Linux_protogen_tool`. Keep those names, because the website's download buttons find the files by them.

The window opens sized to fit your screen and can be resized: the face scales to fill the
space, and the control columns scroll (scrollbar or mouse wheel) when the window is short.

## Displays

Pick the display in the toolbar. It **must match the display the firmware is set up for**.
The **Mk2** (default) and **Mk3** presets ship with the tool; **Open display.json…** loads
any other display config, such as a HUB75 panel, a chain of MAX7219 8×8 matrices or a custom
NeoPixel PCB. The format is documented in
[ProtoFace `docs/display-config.md`](https://github.com/RiotTheClanker/ProtoFace/blob/dev/docs/display-config.md),
with examples in [`displays/examples/`](https://github.com/RiotTheClanker/ProtoFace/tree/dev/displays/examples).
Check a config from the command line with `python display_config.py my_display.json`.

| Display | Left side | Right side |
|---|---|---|
| **Mk2** (default) | Eyes, 3 mouth panels, nose | Eyes, 3 mouth panels |
| **Mk3** | Eyes, 4 mouth panels, nose | Eyes, 4 mouth panels, nose |

Each region (eye, mouth, nose …) is outlined and gets a fill button. Opening a file made for
another display offers to switch to it, or to open its display.json. Switching displays keeps
your artwork, matched panel by panel (eyes → eyes, mouth → mouth, nose → nose).

Files for Mk2 and Mk3 are saved in the same format as before, so released ProtoFace
firmware plays them. Files for other displays use a version 2 header that records the
display's pixel count and layout ID; they need firmware with display-config support, which
is still being built on the `dev` branch.

## Tabs

- **🎨 Painter** — click or drag to paint LEDs. Each LED stores a colour plus a sound mode:
  - *Static* — always on
  - *Snap* — on only when the volume is at or above the threshold
  - *Linear* — brightness = m × volume + b

  **Mirror to other side** (on by default) paints the same pixel on the matching panel of
  the other side. **Copy left → right** copies a whole side. Without one of these, the
  right side stays black. The display config says which sides mirror each other.
  **Flip left↔right when mirroring** (its default comes from the display config; on for
  Mk2 and Mk3, whose right-side panels are mounted mirrored) makes side 2 a mirror image:
  pixels are flipped horizontally and the panel order within each region is reversed
  (left eye ↔ right eye, first mouth panel ↔ last). Leave it off if both sides should show
  the same image in the same orientation. Which one you need depends on how the panels are
  mounted and wired.

  Frames are either *Timed* (shown for `duration_ms`) or *Sound triggered* (advance when the
  volume rises past 128, after at least `duration_ms`).
- **▶ Simulator** — plays a `.anim` file or the Painter frames, following the same timing and
  sound rules as the firmware. Use the volume slider or your microphone.
- **📄 Export .h** — turns one frame into `fallback_anim.h` for the selected display. Copy
  it to `ProtoFace/src/` and rebuild the firmware. The preview shows how many LEDs are lit on each side, so an
  empty side is easy to spot before exporting.

## File format

See the [ProtoFace README](https://github.com/RiotTheClanker/ProtoFace#-animation-file-format-anim).
