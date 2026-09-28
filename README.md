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

## Layouts

Choose the layout in the toolbar. It **must match `PROTOGEN_LAYOUT` in the firmware**
(the firmware defaults to 11).

| Layout | Side 1 — left chain (GP2) | Side 2 — right chain (GP3) |
|---|---|---|
| **11** (default) | Nose side: eyes, 3 mouth panels, nose | Plain side: eyes, 3 mouth panels |
| **14** | Eyes, 4 mouth panels, nose | Eyes, 4 mouth panels, nose |

Both sides are drawn in the Painter and Simulator: side 1 on top, side 2 underneath.
Opening a file made for the other layout offers to switch layouts. Switching layouts
keeps your artwork, matched panel by panel (eyes → eyes, mouth → mouth, nose → nose).

## Tabs

- **🎨 Painter** — click or drag to paint LEDs. Each LED stores a colour plus a sound mode:
  - *Static* — always on
  - *Snap* — on only when the volume is at or above the threshold
  - *Linear* — brightness = m × volume + b

  **Mirror to other side** (on by default) paints the same pixel on the matching panel of
  the other side. **Copy side 1 → side 2** copies a whole side. Without one of these,
  side 2 (the right-hand LED chain) stays black.
  Tick **Flip left↔right when mirroring** if side 2 should be a mirror image of side 1:
  pixels are flipped horizontally and the panel order within each region is reversed
  (left eye ↔ right eye, first mouth panel ↔ last). Leave it off if both sides should show
  the same image in the same orientation. Which one you need depends on how the panels are
  mounted and wired.

  Frames are either *Timed* (shown for `duration_ms`) or *Sound triggered* (advance when the
  volume rises past 128, after at least `duration_ms`).
- **▶ Simulator** — plays a `.anim` file or the Painter frames, following the same timing and
  sound rules as the firmware. Use the volume slider or your microphone.
- **📄 Export .h** — turns one frame into `fallback_anim.h`. Copy it to `ProtoFace/src/`
  and rebuild the firmware. The preview shows how many LEDs are lit on each side, so an
  empty side is easy to spot before exporting.

## File format

See the [ProtoFace README](https://github.com/RiotTheClanker/ProtoFace#-animation-file-format-anim).
