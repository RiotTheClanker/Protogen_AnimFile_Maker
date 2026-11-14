import unittest

import display_config as dcfg
from anim_io import SOUND_STATIC, SOUND_SNAP, SOUND_LINEAR, apply_sound

try:
    import protogen_tool
except ImportError:          # no tkinter / numpy on this machine
    protogen_tool = None


@unittest.skipIf(protogen_tool is None, "needs tkinter and numpy")
class ExampleTest(unittest.TestCase):
    def test_example_uses_all_three_reactions(self):
        for cfg in dcfg.presets().values():
            frames, meta = protogen_tool.make_example(cfg)
            self.assertEqual(len(frames), 2)
            self.assertEqual([m['duration_ms'] for m in meta], [2500, 150])
            modes = {led[3] for led in frames[0] if any(led[:3])}
            self.assertEqual(modes, {SOUND_STATIC, SOUND_SNAP, SOUND_LINEAR}, cfg.name)
            self.assertTrue(all(len(f) == cfg.total_pixels for f in frames))


class ApplySoundTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(apply_sound([10, 20, 30, SOUND_SNAP, 128], 127), (0, 0, 0))
        self.assertEqual(apply_sound([10, 20, 30, SOUND_SNAP, 128], 128), (10, 20, 30))
        self.assertEqual(apply_sound([0, 255, 0, SOUND_LINEAR, 0x10], 255), (0, 255, 0))
        self.assertEqual(apply_sound([0, 255, 0, SOUND_LINEAR, 0x00], 255), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
