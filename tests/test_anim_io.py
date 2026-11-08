"""Checks for anim_io.py.  Run: python -m unittest"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anim_io as aio  # noqa: E402
import display_config as dc  # noqa: E402

HUB75 = {
    'protoface_display': 1, 'name': 'hub',
    'panels': [{'id': 'L', 'w': 64, 'h': 32, 'x': 0, 'y': 0, 'side': 'left', 'region': 'face'},
               {'id': 'R', 'w': 64, 'h': 32, 'x': 64, 'y': 0, 'side': 'right', 'region': 'face'}],
    'outputs': [{'type': 'hub75', 'width': 64, 'height': 32, 'chain': 2, 'pins': {},
                 'place': [{'panel': 'L', 'x': 0, 'y': 0}, {'panel': 'R', 'x': 64, 'y': 0}]}],
}


def frame(n, colour=(0, 0, 0)):
    return {'duration_ms': 250, 'timing_mode': 1,
            'leds': [[*colour, 0, 0] for _ in range(n)]}


class AnimIoTests(unittest.TestCase):
    def setUp(self):
        self.presets = dc.presets()
        self.tmp = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_mk2_saves_version_1(self):
        mk2 = self.presets['Mk2']
        aio.write_anim(self.path('a.anim'), mk2, [frame(704, (1, 2, 3))])
        with open(self.path('a.anim'), 'rb') as f:
            data = f.read()
        self.assertEqual(data[:8], b'ANIM\x01\x0b\x00\x00')    # what V6.2 firmware reads
        self.assertEqual(len(data), 8 + 4 + 704 * 5)
        a = aio.read_anim(self.path('a.anim'))
        self.assertTrue(a.fits(mk2))
        self.assertFalse(a.fits(self.presets['Mk3']))
        self.assertEqual(a.frames[0]['leds'][5], [1, 2, 3, 0, 0])
        self.assertEqual(a.frames[0]['duration_ms'], 250)

    def test_old_tool_file_loads(self):
        # Exactly what tool V4 wrote for layout 14.
        data = b'ANIM' + bytes((1, 14, 0, 0)) + struct.pack('<HBB', 100, 0, 0) + bytes(896 * 5)
        a = aio.parse_anim(data)
        self.assertTrue(a.fits(self.presets['Mk3']))
        self.assertEqual(len(a.frames), 1)

    def test_other_displays_save_version_2(self):
        cfg = dc.DisplayConfig(HUB75)
        aio.write_anim(self.path('h.anim'), cfg, [frame(4096), frame(4096)])
        with open(self.path('h.anim'), 'rb') as f:
            hdr = f.read(16)
        self.assertEqual(hdr[:6], b'ANIM\x02\x00')
        self.assertEqual(struct.unpack('<HII', hdr[6:]), (16, 4096, cfg.layout_id()))
        a = aio.read_anim(self.path('h.anim'))
        self.assertEqual(len(a.frames), 2)
        self.assertTrue(a.fits(cfg))
        self.assertFalse(a.fits(self.presets['Mk2']))

    def test_cut_off_frame_ignored(self):
        mk2 = self.presets['Mk2']
        aio.write_anim(self.path('a.anim'), mk2, [frame(704), frame(704)])
        with open(self.path('a.anim'), 'rb') as f:
            data = f.read()
        self.assertEqual(len(aio.parse_anim(data[:-10]).frames), 1)

    def test_wrong_pixel_count_refused(self):
        with self.assertRaises(ValueError):
            aio.write_anim(self.path('x.anim'), self.presets['Mk2'], [frame(896)])

    def test_rejects_garbage(self):
        for data in (b'nope', b'ANIM\x01\x07\x00\x00', b'ANIM\x09\x00\x00\x00'):
            with self.assertRaises(ValueError):
                aio.parse_anim(data)

    def test_remap_between_helmets(self):
        mk2, mk3 = self.presets['Mk2'], self.presets['Mk3']
        leds = [[0, 0, 0, 0, 0] for _ in range(mk3.total_pixels)]
        leds[mk3.by_id['L_eye2'].offset + 9] = [9, 9, 9, 0, 0]
        leds[mk3.by_id['R_nose'].offset] = [7, 7, 7, 0, 0]      # Mk2's right side has no nose
        leds[mk3.by_id['L_mouth4'].offset] = [5, 5, 5, 0, 0]    # Mk2 has 3 mouth panels
        out = aio.remap_leds(leds, mk3, mk2)
        self.assertEqual(len(out), 704)
        self.assertEqual(out[mk2.by_id['L_eye2'].offset + 9][:3], [9, 9, 9])
        self.assertEqual(sum(1 for led in out if any(led[:3])), 1)


if __name__ == '__main__':
    unittest.main()
