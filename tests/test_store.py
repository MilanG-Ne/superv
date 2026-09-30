import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from superv import store  # noqa: E402
from superv.store import KIND_FILES, KIND_IMAGE, KIND_TEXT, Store, png_size  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x40\x00\x00\x00\xc8" + b"\x00" * 40)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        self.run_dir = os.path.join(self.tmp.name, "run")
        self.store = self.make()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        s = Store(self.data, self.run_dir, **kwargs)
        s.load()
        return s

    def texts(self, s=None):
        return [item.text for item in (s or self.store).items]

    def test_newest_first_and_dedup_moves_to_top(self):
        self.store.add(KIND_TEXT, "one")
        self.store.add(KIND_TEXT, "two")
        self.store.add(KIND_TEXT, "one")
        self.assertEqual(self.texts(), ["one", "two"])

    def test_limit_drops_oldest_unpinned_but_keeps_pinned(self):
        self.store.max_items = 3
        first = self.store.add(KIND_TEXT, "pinned")
        self.store.set_pinned(first.id, True)
        for n in range(5):
            self.store.add(KIND_TEXT, "t%d" % n)
        self.assertEqual(self.texts(), ["t4", "t3", "t2", "pinned"])

    def test_clear_keeps_pinned(self):
        keep = self.store.add(KIND_TEXT, "keep")
        self.store.set_pinned(keep.id, True)
        self.store.add(KIND_TEXT, "drop")
        removed = self.store.clear()
        self.assertEqual([i.text for i in removed], ["drop"])
        self.assertEqual(self.texts(), ["keep"])

    def test_restart_in_same_session_keeps_everything(self):
        self.store.add(KIND_TEXT, "a")
        self.store.add(KIND_TEXT, "b")
        self.assertEqual(self.texts(self.make()), ["b", "a"])

    def test_reboot_keeps_only_pinned(self):
        pinned = self.store.add(KIND_IMAGE, blobs={"image/png": PNG})
        self.store.set_pinned(pinned.id, True)
        self.store.add(KIND_TEXT, "gone after reboot")
        # A reboot empties the runtime directory.
        for name in os.listdir(os.path.join(self.run_dir, "blobs")):
            os.unlink(os.path.join(self.run_dir, "blobs", name))
        os.unlink(os.path.join(self.run_dir, store.SESSION_INDEX))
        after = self.make()
        self.assertEqual([i.kind for i in after.items], [KIND_IMAGE])
        self.assertEqual(after.read_blob(after.items[0], "image/png"), PNG)

    def test_stale_session_from_previous_boot_is_ignored(self):
        self.store.add(KIND_TEXT, "old boot")
        with mock.patch.object(store, "boot_id", return_value="another-boot"):
            self.assertEqual(self.texts(self.make()), [])

    def test_keep_history_after_restart(self):
        self.store.set_persist_all(True)
        self.store.add(KIND_TEXT, "kept")
        os.unlink(os.path.join(self.run_dir, store.SESSION_INDEX))
        self.assertEqual(self.texts(self.make(persist_all=True)), ["kept"])

    def test_pinning_moves_blobs_to_persistent_dir(self):
        item = self.store.add(KIND_IMAGE, blobs={"image/png": PNG})
        self.assertTrue(self.store.blob_path(item, "image/png").startswith(self.run_dir))
        self.store.set_pinned(item.id, True)
        self.assertTrue(self.store.blob_path(item, "image/png").startswith(self.data))
        self.store.set_pinned(item.id, False)
        self.assertTrue(self.store.blob_path(item, "image/png").startswith(self.run_dir))

    def test_persistent_index_holds_only_pinned_items(self):
        item = self.store.add(KIND_TEXT, "secret-ish")
        with open(os.path.join(self.data, store.PERSISTENT_INDEX)) as handle:
            self.assertEqual(json.load(handle)["items"], [])
        self.store.set_pinned(item.id, True)
        with open(os.path.join(self.data, store.PERSISTENT_INDEX)) as handle:
            self.assertEqual([i["text"] for i in json.load(handle)["items"]], ["secret-ish"])

    def test_remove_deletes_blobs(self):
        item = self.store.add(KIND_TEXT, "rich", {"text/html": b"<b>rich</b>"})
        path = self.store.blob_path(item, "text/html")
        self.assertTrue(os.path.exists(path))
        self.store.remove(item.id)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.store.items, [])

    def test_recopy_replaces_formats(self):
        item = self.store.add(KIND_TEXT, "same", {"text/html": b"<i>same</i>"})
        again = self.store.add(KIND_TEXT, "same")
        self.assertIs(item, again)
        self.assertEqual(again.formats, [])
        self.assertIsNone(self.store.read_blob(again, "text/html"))

    def test_image_size_from_png_header(self):
        self.assertEqual(png_size(PNG), (320, 200))
        item = self.store.add(KIND_IMAGE, blobs={"image/png": PNG})
        self.assertEqual((item.width, item.height), (320, 200))

    def test_files_item_round_trip(self):
        data = b"copy\nfile:///tmp/a.txt"
        item = self.store.add(KIND_FILES, "/tmp/a.txt", {"x-special/gnome-copied-files": data})
        self.assertEqual(self.store.read_blob(item, "x-special/gnome-copied-files"), data)
        self.assertEqual(self.make().items[0].kind, KIND_FILES)

    def test_orphan_blobs_are_removed(self):
        os.makedirs(os.path.join(self.run_dir, "blobs"), exist_ok=True)
        orphan = os.path.join(self.run_dir, "blobs", "deadbeef.image%2Fpng")
        open(orphan, "wb").close()
        self.make()
        self.assertFalse(os.path.exists(orphan))

    def test_corrupt_index_is_ignored(self):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, store.PERSISTENT_INDEX), "w") as handle:
            handle.write("{not json")
        self.assertEqual(self.make().items, [])

    def test_bad_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            store.Item.from_dict({"id": "../../etc", "kind": KIND_TEXT})

    def test_touch_moves_to_top(self):
        a = self.store.add(KIND_TEXT, "a")
        self.store.add(KIND_TEXT, "b")
        self.store.touch(a.id)
        self.assertEqual(self.texts(), ["a", "b"])

    def test_files_are_private(self):
        self.store.add(KIND_TEXT, "x")
        mode = os.stat(os.path.join(self.run_dir, store.SESSION_INDEX)).st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
