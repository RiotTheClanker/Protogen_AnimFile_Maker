import os, tempfile, unittest

try:
    import help_ui
except ImportError:          # no tkinter on this machine
    help_ui = None


@unittest.skipIf(help_ui is None, "needs tkinter")
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


@unittest.skipIf(help_ui is None, "needs tkinter")
class HelpTextTest(unittest.TestCase):
    def test_bold_markers_are_paired(self):
        for title, body in help_ui.HELP_TOPICS:
            for line in (body or '').splitlines():
                self.assertEqual(line.count('**') % 2, 0, f"{title}: {line}")

    def test_embedded_demos_exist(self):
        import help_demos
        for title, body in help_ui.HELP_TOPICS:
            for line in (body or '').splitlines():
                if line.startswith('[[demo:'):
                    self.assertIn(line[7:-2], help_demos.DEMOS, title)

    def test_shortcuts_topic_exists(self):
        self.assertIn(None, [body for _t, body in help_ui.HELP_TOPICS])


if __name__ == '__main__':
    unittest.main()
