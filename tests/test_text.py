import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from superv import pickers
    from superv.monitor import _uri_list_to_paths, html_to_text
    from superv.popup import preview_text
except (ImportError, ValueError) as error:  # no PyGObject / GTK 3 available
    SKIP = "GTK 3 bindings not available: %s" % error
else:
    SKIP = None


@unittest.skipIf(SKIP, SKIP)
class TextHelpersTest(unittest.TestCase):
    def test_html_to_text(self):
        html = b"<p>Hello <b>world</b></p><ul><li>one</li><li>two &amp; three</li></ul><script>x()</script>"
        self.assertEqual(html_to_text(html), "Hello world\none\ntwo & three")

    def test_html_to_text_utf16(self):
        self.assertEqual(html_to_text("<i>Firefox</i>".encode("utf-16")), "Firefox")

    def test_uri_list(self):
        data = b"copy\nfile:///home/me/My%20File.txt\n# comment\nfile:///tmp/b"
        self.assertEqual(_uri_list_to_paths(data), ["/home/me/My File.txt", "/tmp/b"])

    def test_web_links_are_not_files(self):
        self.assertIsNone(_uri_list_to_paths(b"https://example.com/page"))
        self.assertIsNone(_uri_list_to_paths(b""))

    def test_preview_is_short(self):
        text = "\n\n" + "\n".join("line %d\twith tab" % n for n in range(50))
        preview = preview_text(text)
        self.assertEqual(preview.count("\n"), 3)
        self.assertTrue(preview.startswith("line 0"))
        self.assertNotIn("\t", preview)

    def test_search_ranking_prefers_whole_words(self):
        rank = pickers.PickerPage._rank
        self.assertLess(rank("euro", "€", "euro sign"), rank("euro", "₠", "euro-currency sign"))
        self.assertEqual(rank("rocket", "🚀", "rocket"), 0)

    def test_data_files_load(self):
        emoji = pickers.load_groups("emoji.json")
        self.assertGreater(sum(len(g["items"]) for g in emoji), 1500)
        self.assertGreater(len(pickers.load_groups("kaomoji.json")), 10)
        symbols = pickers.symbol_groups()
        self.assertIn("€", [entry[0] for group in symbols for entry in group["items"]])


if __name__ == "__main__":
    unittest.main()
