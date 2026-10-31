"""Tests for userfiles.py: save-folder settings, result naming, the storage
report and the leftover cleaner. Run from the project folder with:

    python -m unittest tests.test_userfiles
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('NEXTGENUP_DATA', tempfile.mkdtemp())

import userfiles  # noqa: E402


def touch(path, size=100, age=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'x' * size)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


class NamingTests(unittest.TestCase):
    def test_video_upscale_name(self):
        task = {'filename': 'My Holiday.mov', 'input': 'x'}
        self.assertEqual(userfiles.result_name(task, '/o/abc_basic_4k.mp4'), 'My Holiday_4K.mp4')
        self.assertEqual(userfiles.result_name(task, '/o/abc_pro_4k.mp4'), 'My Holiday_4K.mp4')

    def test_tool_suffix_and_image_names(self):
        task = {'filename': 'cat.jpg', 'input': 'x', 'name_suffix': '_nobg'}
        self.assertEqual(userfiles.result_name(task, '/o/img_a_nobg.png'), 'cat_nobg.png')
        task = {'filename': 'cat.jpg', 'input': 'x'}
        self.assertEqual(userfiles.result_name(task, '/o/img_a_upscaled.png'), 'cat_upscaled.png')

    def test_child_results_keep_their_display_name(self):
        task = {'filename': 'song_vocals.wav'}   # no 'input': a registered child result
        self.assertEqual(userfiles.result_name(task, '/o/x_voc.wav'), 'song_vocals.wav')

    def test_denoised_audio(self):
        task = {'filename': 'voice.mp3', 'input': 'x'}
        self.assertEqual(userfiles.result_name(task, '/o/x.wav'), 'voice_denoised.wav')

    def test_unique_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            touch(os.path.join(d, 'a.mp4'))
            touch(os.path.join(d, 'a_2.mp4'))
            self.assertEqual(os.path.basename(userfiles.unique_path(d, 'a.mp4')), 'a_3.mp4')
            self.assertEqual(os.path.basename(userfiles.unique_path(d, 'new.mp4')), 'new.mp4')

    def test_unsafe_characters_are_replaced(self):
        self.assertEqual(userfiles.safe_filename('a/b:c*d.mp4'), 'a_b_c_d.mp4')
        self.assertEqual(userfiles.safe_filename('..'), 'result')


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._orig = userfiles.SETTINGS_PATH
        self.tmp = tempfile.mkdtemp()
        userfiles.SETTINGS_PATH = os.path.join(self.tmp, 'settings.json')

    def tearDown(self):
        userfiles.SETTINGS_PATH = self._orig

    def test_defaults(self):
        s = userfiles.load_settings()
        self.assertTrue(s['auto_save'])
        self.assertTrue(s['save_dir'].endswith('NextGenUp'))

    def test_round_trip_and_validation(self):
        target = os.path.join(self.tmp, 'out')
        _, err = userfiles.update_settings(auto_save=False, save_dir=target)
        self.assertIsNone(err)
        self.assertTrue(os.path.isdir(target))
        again = userfiles.load_settings()
        self.assertFalse(again['auto_save'])
        self.assertEqual(again['save_dir'], target)
        _, err = userfiles.update_settings(save_dir='relative/path')
        self.assertIn('full path', err)

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(userfiles.SETTINGS_PATH, 'w') as f:
            f.write('{not json')
        self.assertTrue(userfiles.load_settings()['auto_save'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.up = os.path.join(self.root, 'uploads')
        self.out = os.path.join(self.root, 'outputs')
        self.fr = os.path.join(self.root, 'frames')
        for d in (self.up, self.out, self.fr):
            os.makedirs(d)

    def buckets(self, tasks, active=()):
        return userfiles.classify(self.up, self.out, self.fr, tasks, set(active))

    def test_buckets_and_cleaning(self):
        hour = 3600
        touch(os.path.join(self.up, 'old1_clip.mp4'), 400, age=hour)          # leftover
        touch(os.path.join(self.fr, 'old1', 'f1.png'), 150, age=hour)         # leftover
        touch(os.path.join(self.up, 'run1_big.mp4'), 700, age=hour)           # running job
        touch(os.path.join(self.up, 'new1_clip.mp4'), 50)                     # too new to judge
        saved = os.path.join(self.out, 'a_basic_4k.mp4')
        touch(saved, 500, age=hour)
        unsaved = os.path.join(self.out, 'b_nobg.png')
        touch(unsaved, 300, age=hour)
        tasks = {
            'a': {'status': 'completed', 'output': saved, 'saved': True},
            'b': {'status': 'completed', 'output': unsaved},
            'run1': {'status': 'processing'},
        }
        s = userfiles.summarize(self.buckets(tasks, active={'run1'}))
        self.assertEqual(s['leftovers'], {'files': 3, 'bytes': 400 + 150 + 500})
        self.assertEqual(s['unsaved'], {'files': 1, 'bytes': 300})
        self.assertEqual(s['protected'], {'files': 2, 'bytes': 700 + 50})

        res = userfiles.clean(self.buckets(tasks, active={'run1'}))
        self.assertEqual(res, {'files': 3, 'bytes': 1050})
        self.assertTrue(os.path.exists(unsaved))                  # unsaved results are opt-in
        self.assertTrue(os.path.exists(os.path.join(self.up, 'run1_big.mp4')))
        self.assertTrue(os.path.exists(os.path.join(self.up, 'new1_clip.mp4')))

        res = userfiles.clean(self.buckets(tasks, active={'run1'}), include_unsaved=True)
        self.assertEqual(res['files'], 1)
        self.assertFalse(os.path.exists(unsaved))

    def test_purge_folder_empties_but_keeps_folder(self):
        touch(os.path.join(self.up, 'a.mp4'))
        touch(os.path.join(self.fr, 'x', 'y.png'))
        userfiles.purge_folder(self.up)
        userfiles.purge_folder(self.fr)
        self.assertEqual(os.listdir(self.up), [])
        self.assertEqual(os.listdir(self.fr), [])

    def test_within_blocks_escapes(self):
        base = os.path.join(self.root, 'saved')
        os.makedirs(base)
        self.assertTrue(userfiles.within(os.path.join(base, 'a.mp4'), base))
        self.assertFalse(userfiles.within(os.path.join(base, '..', 'secret'), base))
        self.assertFalse(userfiles.within('/etc/hosts', base))


if __name__ == '__main__':
    unittest.main()
