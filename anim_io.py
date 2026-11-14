"""
anim_io.py  —  read and write ProtoFace .anim files for any display config

File header, version 1 (8 bytes, made by tool V4 and earlier):
    "ANIM", version=1, panel count (11 or 14), 0, 0
File header, version 2 (16 bytes):
    "ANIM", version=2, 0, header size (uint16 LE, 16),
    pixel count (uint32 LE), layout ID (uint32 LE)
Then frames: duration_ms (uint16 LE), timing_mode, 0, and 5 bytes per pixel
(r, g, b, sound_mode, param) in the display config's panel order.

A display made of 11 or 14 plain 8×8 panels is still saved as version 1, so
ProtoFace V6.2 and earlier keep playing those files.
"""

import struct

MAGIC = b'ANIM'
LED_BYTES = 5
FRAME_HDR_SIZE = 4
V1_HEADER_SIZE = 8
V2_HEADER_SIZE = 16
V1_PANEL_COUNTS = (11, 14)
V1_LEDS_PER_PANEL = 64

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


def apply_sound(led, vol):
    """(r, g, b) an LED shows at volume `vol` (0-255), as the firmware does."""
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


def pack_linear(m, b):
    return (max(0, min(15, int(m))) << 4) | max(0, min(15, int(b)))


def blank_led():
    return [0, 0, 0, SOUND_STATIC, 0]


def blank_leds(n):
    return [blank_led() for _ in range(n)]


def _u8(v):
    return max(0, min(255, int(v)))


def make_frame_bytes(duration_ms, timing_mode, leds):
    data = bytearray(struct.pack('<HBB', max(0, min(0xFFFF, int(duration_ms))),
                                 _u8(timing_mode), 0))
    for led in leds:
        r, g, b, sm, p = led[:5]
        data += bytes((_u8(r), _u8(g), _u8(b), _u8(sm), _u8(p)))
    return bytes(data)


def header_for(cfg):
    """File header bytes for frames made with display config cfg."""
    v1 = next((n for n in V1_PANEL_COUNTS if cfg.is_v1_compatible(n)), None)
    if v1 is not None:
        return MAGIC + struct.pack('BBBB', 1, v1, 0, 0)
    return MAGIC + struct.pack('<BBHII', 2, 0, V2_HEADER_SIZE,
                               cfg.total_pixels, cfg.layout_id())


def write_anim(path, cfg, frames):
    """frames: list of {'duration_ms', 'timing_mode', 'leds'}."""
    with open(path, 'wb') as f:
        f.write(header_for(cfg))
        for fr in frames:
            if len(fr['leds']) != cfg.total_pixels:
                raise ValueError(f"frame has {len(fr['leds'])} pixels, "
                                 f"display \"{cfg.name}\" has {cfg.total_pixels}")
            f.write(make_frame_bytes(fr['duration_ms'], fr['timing_mode'], fr['leds']))


class AnimFile:
    """What a .anim file says about the display it was made for, plus its frames."""

    def __init__(self, version, pixels, layout_id=None, v1_panels=None, frames=()):
        self.version = version
        self.pixels = pixels
        self.layout_id = layout_id
        self.v1_panels = v1_panels
        self.frames = list(frames)

    def fits(self, cfg):
        if self.version == 1:
            return cfg.is_v1_compatible(self.v1_panels)
        return self.pixels == cfg.total_pixels and self.layout_id == cfg.layout_id()

    def describe(self):
        if self.version == 1:
            return f"a {self.v1_panels}-panel helmet ({'Mk2' if self.v1_panels == 11 else 'Mk3'})"
        return f"a display with {self.pixels} pixels (layout ID {self.layout_id:08x})"


def read_anim(path):
    with open(path, 'rb') as f:
        data = f.read()
    return parse_anim(data)


def parse_anim(data):
    if len(data) < V1_HEADER_SIZE or data[:4] != MAGIC:
        raise ValueError("Not a valid .anim file")
    version = data[4]
    if version == 1:
        panels = data[5]
        if panels not in V1_PANEL_COUNTS:
            raise ValueError(f"Unknown panel count {panels} in the file header "
                             f"(version 1 files are 11 or 14 panels)")
        info = AnimFile(1, panels * V1_LEDS_PER_PANEL, v1_panels=panels)
        pos = V1_HEADER_SIZE
    elif version == 2:
        if len(data) < V2_HEADER_SIZE:
            raise ValueError("The .anim header is cut short")
        _, _, hdr_size, pixels, layout = struct.unpack_from('<BBHII', data, 4)
        if hdr_size < V2_HEADER_SIZE or pixels == 0:
            raise ValueError("The .anim header is damaged")
        info = AnimFile(2, pixels, layout_id=layout)
        pos = hdr_size
    else:
        raise ValueError(f".anim version {version} is newer than this tool; update the tool")

    frame_size = FRAME_HDR_SIZE + info.pixels * LED_BYTES
    while pos + frame_size <= len(data):     # a cut-off last frame is ignored, as in the firmware
        raw = data[pos:pos + frame_size]
        info.frames.append({
            'duration_ms': struct.unpack_from('<H', raw, 0)[0],
            'timing_mode': raw[2],
            'leds': [list(raw[o:o + LED_BYTES])
                     for o in range(FRAME_HDR_SIZE, frame_size, LED_BYTES)],
        })
        pos += frame_size
    return info


def remap_leds(leds, src, dst):
    """
    Convert one frame between display configs.  Panels are matched by side
    and region, then by order within the region (eye 1 → eye 1, mouth 2 →
    mouth 2); panels with no region are matched by id.  Pixels are copied at
    the same x, y where both panels have one; everything else starts blank.
    """
    def keyed(cfg):
        out, count = {}, {}
        for p in cfg.panels:
            if p.region is None:
                out[('id', p.id)] = p
            else:
                k = (p.side, p.region)
                i = count.get(k, 0)
                count[k] = i + 1
                out[(p.side, p.region, i)] = p
        return out

    out = blank_leds(dst.total_pixels)
    dst_panels = keyed(dst)
    for key, a in keyed(src).items():
        b = dst_panels.get(key)
        if b is None:
            continue
        for i, (x, y) in enumerate(a.pixels):
            j = b.index_of(x, y)
            if j is not None and a.offset + i < len(leds):
                out[b.offset + j] = list(leds[a.offset + i])
    return out
