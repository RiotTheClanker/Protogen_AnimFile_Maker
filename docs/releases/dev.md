## Protogen Animation Tool dev test build

> ⚠️ **Test build from the `dev` branch.** Use it with the ProtoFace dev firmware
> (V7.0-dev). For everyday use with V6.2 stay on **V4**.

Downloads: **Windows** `Windows_protogen_tool.exe` · **macOS** `Mac_protogen_tool` · **Linux** `Linux_protogen_tool`

### New
- **Any display.** Pick Mk2 or Mk3 in the toolbar, or **Open display.json…** to draw for a
  HUB75 panel, MAX7219 8×8 matrices or a custom NeoPixel PCB. Use the same `display.json`
  that is on the helmet's SD card (example files are attached to the ProtoFace dev release).
- `.anim` files for Mk2 / Mk3 keep the old format and still play on V6.2. Files for other
  displays use a new version 2 header that only the dev firmware reads.
- **Help guide** with live demos, tooltips, keyboard shortcuts and undo.

### Known
- **Export .h** is from the old built-in fallback face and is not needed by the new firmware.
