"""Checks for display_config.py and the shipped presets.  Run: python -m unittest"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import display_config as dc  # noqa: E402

# The hard-coded layouts in protogen_tool.py (V4) and the firmware's default
# panel maps (ProtoFace V6.2).  The presets must describe exactly these.
TOOL_LAYOUTS = {
    'Mk2': [dict(eyes=[0, 1], mouth=[2, 3, 4], nose=5),
            dict(eyes=[6, 7], mouth=[8, 9, 10], nose=None)],
    'Mk3': [dict(eyes=[0, 1], mouth=[2, 3, 4, 5], nose=6),
            dict(eyes=[7, 8], mouth=[9, 10, 11, 12], nose=13)],
}
# DEFAULT_MAP_L/R: chain position of each logical panel on that side.
FIRMWARE_MAPS = {
    'Mk2': ([0, 1, 2, 3, 4, 5], [3, 4, 0, 1, 2]),
    'Mk3': ([6, 5, 4, 3, 2, 1, 0], [0, 1, 2, 3, 4, 5, 6]),
}


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.presets = dc.presets()

    def test_both_presets_ship(self):
        self.assertEqual(sorted(self.presets), ['Mk2', 'Mk3'])

    def test_v1_compatible(self):
        self.assertTrue(self.presets['Mk2'].is_v1_compatible(11))
        self.assertTrue(self.presets['Mk3'].is_v1_compatible(14))
        self.assertFalse(self.presets['Mk2'].is_v1_compatible(14))

    def test_panel_roles_match_tool(self):
        for name, sides in TOOL_LAYOUTS.items():
            cfg = self.presets[name]
            idx = {p.id: i for i, p in enumerate(cfg.panels)}
            for side_name, side in zip(('left', 'right'), sides):
                regions = cfg.regions(side_name)
                self.assertEqual([idx[p.id] for p in regions['eye']], side['eyes'], name)
                self.assertEqual([idx[p.id] for p in regions['mouth']], side['mouth'], name)
                nose = [idx[p.id] for p in regions.get('nose', [])]
                self.assertEqual(nose, [] if side['nose'] is None else [side['nose']], name)

    def test_chain_order_matches_firmware(self):
        for name, maps in FIRMWARE_MAPS.items():
            cfg = self.presets[name]
            for out, side_map in zip(cfg.outputs, maps):
                side_panels = [p.id for p in cfg.panels if p.side == out['name']]
                chain = [e if isinstance(e, str) else e['panel'] for e in out['chain']]
                # logical panel i sits at chain position side_map[i]
                expect = [None] * len(side_map)
                for i, pos in enumerate(side_map):
                    expect[pos] = side_panels[i]
                self.assertEqual(chain, expect, f"{name} {out['name']}")
                self.assertEqual(out['pin'], {'left': 2, 'right': 3}[out['name']])

    def test_mirror_flip_matches_tool(self):
        # Tool: with flip, left eye1 pixel (x, y) -> right eye2 pixel (7-x, y).
        cfg = self.presets['Mk3']
        m = cfg.mirror_map()
        l_eye1 = cfg.by_id['L_eye1'].offset
        r_eye2 = cfg.by_id['R_eye2'].offset
        self.assertEqual(m[l_eye1 + 0], r_eye2 + 7)
        self.assertEqual(m[r_eye2 + 7], l_eye1 + 0)
        # without flip: same pixel on the same-numbered panel
        m = cfg.mirror_map(flip='none')
        self.assertEqual(m[l_eye1 + 3], cfg.by_id['R_eye1'].offset + 3)

    def test_mk2_nose_has_no_mirror(self):
        cfg = self.presets['Mk2']
        nose = cfg.by_id['L_nose']
        self.assertNotIn(nose.offset, cfg.mirror_map())


class ValidationTests(unittest.TestCase):
    def base(self):
        with open(os.path.join(dc.PRESET_DIR, 'mk2.json')) as f:
            return json.load(f)

    def assertRejected(self, data, text):
        with self.assertRaises(dc.ConfigError) as cm:
            dc.validate(data)
        self.assertIn(text, str(cm.exception))

    def test_unused_panel(self):
        d = self.base()
        d['outputs'][1]['chain'].pop()
        self.assertRejected(d, 'not shown by any output')

    def test_panel_used_twice(self):
        d = self.base()
        d['outputs'][1]['chain'].append('L_eye1')
        self.assertRejected(d, 'is used by')

    def test_unknown_panel(self):
        d = self.base()
        d['outputs'][0]['chain'][0] = 'nope'
        self.assertRejected(d, 'unknown panel')

    def test_pixel_outside_panel(self):
        d = self.base()
        d['panels'][0]['pixels'] = [[0, 0], [8, 0]]
        self.assertRejected(d, 'outside')

    def test_too_many_pixels(self):
        d = self.base()
        d['panels'][0]['w'] = d['panels'][0]['h'] = 256
        self.assertRejected(d, 'limit')

    def test_max7219_needs_8x8(self):
        d = self.base()
        d['outputs'][0]['type'] = 'max7219'
        d['outputs'][0]['pins'] = {'din': 7, 'clk': 6, 'cs': 8}
        d['panels'][0]['w'] = 16
        self.assertRejected(d, '8x8')

    def test_hub75_fit(self):
        d = {
            'protoface_display': 1, 'name': 'h',
            'panels': [{'id': 'a', 'w': 64, 'h': 32, 'x': 0, 'y': 0}],
            'outputs': [{'type': 'hub75', 'width': 64, 'height': 32, 'chain': 1,
                         'pins': {}, 'place': [{'panel': 'a', 'x': 1, 'y': 0}]}],
        }
        self.assertRejected(d, 'does not fit')
        d['outputs'][0]['place'][0]['x'] = 0
        dc.validate(d)


class LayoutIdTests(unittest.TestCase):
    def test_wiring_and_names_do_not_change_it(self):
        cfg = dc.presets()['Mk2']
        d = copy.deepcopy(cfg.data)
        d['panels'][0]['id'] = 'renamed'
        d['outputs'][0]['chain'][0] = 'renamed'
        d['outputs'][1]['chain'].reverse()
        d['panels'][3]['x'] = 50
        self.assertEqual(dc.DisplayConfig(d).layout_id(), cfg.layout_id())

    def test_sizes_change_it(self):
        ids = {c.layout_id() for c in dc.presets().values()}
        self.assertEqual(len(ids), 2)

    def test_known_value(self):
        # Pinned so the firmware's C implementation can be checked against it.
        self.assertEqual(dc.presets()['Mk2'].layout_id(), MK2_LAYOUT_ID)


MK2_LAYOUT_ID = 0x2F5051C2


class ExampleTests(unittest.TestCase):
    """ProtoFace's displays/examples, when the repo is checked out next to this one."""
    EXAMPLES = os.path.join(os.path.dirname(dc.PRESET_DIR), '..', 'ProtoFace', 'displays', 'examples')

    @unittest.skipUnless(os.path.isdir(EXAMPLES), 'ProtoFace checkout not found')
    def test_examples_load(self):
        for fn in os.listdir(self.EXAMPLES):
            dc.load(os.path.join(self.EXAMPLES, fn))


if __name__ == '__main__':
    unittest.main()
