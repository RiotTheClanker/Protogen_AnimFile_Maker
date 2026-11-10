import os, tempfile, unittest

import help_ui


class SettingsTest(unittest.TestCase):
    def test_defaults_then_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), 'sub', 'settings.json')
        s = help_ui.Settings(path)
        self.assertFalse(s.get('guide_seen'))
        s.set('guide_seen', True)
        self.assertTrue(help_ui.Settings(path).get('guide_seen'))

    def test_broken_file_uses_defaults(self):
        path = os.path.join(tempfile.mkdtemp(), 'settings.json')
        with open(path, 'w') as fh:
            fh.write('not json')
        self.assertFalse(help_ui.Settings(path).get('show_guide_at_start'))


class HelpTextTest(unittest.TestCase):
    def test_bold_markers_are_paired(self):
        for title, body in help_ui.HELP_TOPICS:
            for line in (body or '').splitlines():
                self.assertEqual(line.count('**') % 2, 0, f"{title}: {line}")

    def test_shortcuts_topic_exists(self):
        self.assertIn(None, [body for _t, body in help_ui.HELP_TOPICS])


if __name__ == '__main__':
    unittest.main()
