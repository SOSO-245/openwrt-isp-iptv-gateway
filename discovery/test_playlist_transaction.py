"""Failure injection in temporary files only; no live playlists or network."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import playlist_update as update


class PlaylistTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.www = self.root / 'www'
        self.www.mkdir()
        self.backups = self.root / 'backups'
        self.old = {name: ('old ' + name).encode() for name in update.PLAYLISTS}
        self.new = {name: 'new ' + name for name in update.PLAYLISTS}
        for name, data in self.old.items():
            (self.www / name).write_bytes(data)
        planner = patch.object(update, 'plan', return_value=(self.new, [{'kind': 'add'}], []))
        planner.start()
        self.addCleanup(planner.stop)
        self.write = update.atomic_bytes
        self.save = update.save_manifest

    def apply(self):
        return update.apply(self.www, self.backups, [])

    def manifest(self):
        return json.loads(next(self.backups.glob('update-*/manifest.json')).read_text())

    def snapshot(self):
        return {name: update.read_optional(self.www / name) for name in update.PLAYLISTS}

    def assert_blocked(self):
        before = self.snapshot()
        with patch.object(update, 'atomic_bytes') as write, self.assertRaisesRegex(ValueError, '暂停'):
            self.apply()
        write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_success_has_private_durable_manifest_with_both_hash_sets(self):
        def write(path, data, mode=0o644):
            if path.parent == self.www:
                manifest = self.manifest()
                self.assertEqual(manifest['state'], 'prepared')
                for name in update.PLAYLISTS:
                    self.assertEqual(manifest['before'][name], hashlib.sha256(self.old[name]).hexdigest())
                    self.assertEqual(manifest['after'][name], hashlib.sha256(self.new[name].encode()).hexdigest())
            self.write(path, data, mode)

        with patch.object(update, 'atomic_bytes', side_effect=write), \
                patch.object(update, 'sync_directory', wraps=update.sync_directory) as sync:
            result = self.apply()
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(self.manifest()['state'], 'applied')
        self.assertIn(self.www, [call.args[0] for call in sync.call_args_list])
        self.assertEqual((Path(result['backup']) / 'manifest.json').stat().st_mode & 0o777, 0o600)

    def test_prepared_record_failure_never_touches_live_files(self):
        with patch.object(update, 'save_manifest', side_effect=OSError('full')), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.snapshot(), self.old)

    def test_preflight_conflict_cancels_without_overwriting_external_edit(self):
        def save(backup, manifest):
            self.save(backup, manifest)
            if manifest['state'] == 'prepared':
                (self.www / 'new.m3u').write_bytes(b'external')

        with patch.object(update, 'save_manifest', side_effect=save), self.assertRaisesRegex(ValueError, '其他任务'):
            self.apply()
        self.assertEqual(self.snapshot(), self.old | {'new.m3u': b'external'})
        self.assertEqual(self.manifest()['state'], 'cancelled')

    def test_mid_write_external_edit_is_not_rolled_back(self):
        def write(path, data, mode=0o644):
            if path == self.www / 'new-logo.m3u':
                (self.www / 'new.m3u').write_bytes(b'external')
                raise OSError('write failed')
            self.write(path, data, mode)

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaisesRegex(OSError, '未覆盖外部'):
            self.apply()
        self.assertEqual(self.snapshot(), self.old | {'new.m3u': b'external'})
        self.assertEqual(self.manifest()['conflicts'], ['new.m3u'])
        self.assertEqual(self.manifest()['state'], 'needs_review')
        self.assert_blocked()

    def test_external_deletion_is_not_recreated_by_rollback(self):
        def write(path, data, mode=0o644):
            if path == self.www / 'new-logo.m3u':
                (self.www / 'new.m3u').unlink()
                raise OSError('write failed')
            self.write(path, data, mode)

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaises(OSError):
            self.apply()
        self.assertIsNone(self.snapshot()['new.m3u'])
        self.assert_blocked()

    def test_rechecks_unwritten_files_and_restores_only_owned_file(self):
        def write(path, data, mode=0o644):
            self.write(path, data, mode)
            if path == self.www / 'new.m3u' and data == self.new[path.name].encode():
                (self.www / 'new-logo.m3u').write_bytes(b'external')

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.snapshot(), self.old | {'new-logo.m3u': b'external'})
        self.assert_blocked()

    def test_failure_after_rename_also_rolls_back_attempted_file(self):
        (self.www / 'new.txt').unlink()
        def write(path, data, mode=0o644):
            self.write(path, data, mode)
            if path == self.www / 'new.txt':
                raise OSError('directory fsync failed after rename')

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.snapshot(), self.old | {'new.txt': None})
        self.assertEqual(self.manifest()['state'], 'rolled_back')

    def test_one_rollback_failure_does_not_prevent_other_restores(self):
        def write(path, data, mode=0o644):
            if path == self.www / 'new.txt':
                raise OSError('forward write failed')
            if path == self.www / 'new-logo.m3u' and data == self.old[path.name]:
                raise OSError('rollback failed')
            self.write(path, data, mode)

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.snapshot()['new.m3u'], self.old['new.m3u'])
        self.assertEqual(self.manifest()['rollback_failed'], ['new-logo.m3u'])
        self.assert_blocked()

    def test_final_journal_failure_rolls_back_with_a_record(self):
        def save(backup, manifest):
            if manifest['state'] == 'applied':
                raise OSError('final journal failed')
            self.save(backup, manifest)

        with patch.object(update, 'save_manifest', side_effect=save), self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.snapshot(), self.old)
        self.assertEqual(self.manifest()['state'], 'rolled_back')

    def test_interruption_at_each_forward_stage_blocks_new_updates(self):
        for position in range(4):
            with self.subTest(position=position), tempfile.TemporaryDirectory() as directory:
                # Each subcase represents a separate device restart with no runtime state.
                self.backups = Path(directory) / 'backups'
                for name, data in self.old.items():
                    (self.www / name).write_bytes(data)
                def write(path, data, mode=0o644):
                    if path.parent == self.www and update.PLAYLISTS.index(path.name) == position:
                        raise KeyboardInterrupt('simulated abrupt stop')
                    self.write(path, data, mode)
                def save(backup, manifest):
                    if position == 3 and manifest['state'] == 'applied':
                        raise KeyboardInterrupt('simulated abrupt stop')
                    self.save(backup, manifest)
                with patch.object(update, 'atomic_bytes', side_effect=write), \
                        patch.object(update, 'save_manifest', side_effect=save), self.assertRaises(KeyboardInterrupt):
                    self.apply()
                self.assertEqual(self.manifest()['state'], 'prepared')
                self.assert_blocked()

    def test_interruption_during_rollback_keeps_unfinished_marker(self):
        def write(path, data, mode=0o644):
            if path == self.www / 'new-logo.m3u':
                raise OSError('forward write failed')
            if path == self.www / 'new.m3u' and data == self.old[path.name]:
                raise KeyboardInterrupt('simulated stop during rollback')
            self.write(path, data, mode)

        with patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaises(KeyboardInterrupt):
            self.apply()
        self.assertEqual(self.manifest()['state'], 'rolling_back')
        self.assert_blocked()

    def test_failed_rollback_journal_does_not_start_untracked_rollback(self):
        def save(backup, manifest):
            if manifest['state'] == 'rolling_back':
                raise OSError('journal unavailable')
            self.save(backup, manifest)
        def write(path, data, mode=0o644):
            if path == self.www / 'new-logo.m3u':
                raise OSError('forward write failed')
            self.write(path, data, mode)

        with patch.object(update, 'save_manifest', side_effect=save), \
                patch.object(update, 'atomic_bytes', side_effect=write), self.assertRaisesRegex(OSError, '未自动回退'):
            self.apply()
        self.assertEqual(self.snapshot()['new.m3u'], self.new['new.m3u'].encode())
        self.assertEqual(self.manifest()['state'], 'prepared')
        self.assert_blocked()

    def test_invalid_or_legacy_unfinished_manifest_blocks_even_no_changes(self):
        backup = self.backups / 'update-test'
        backup.mkdir(parents=True)
        for text in ('not json', '[]', '{"state":"unknown"}', '{"state":"prepared"}'):
            with self.subTest(text=text):
                (backup / 'manifest.json').write_text(text)
                with patch.object(update, 'plan', return_value=({}, [], [])) as plan:
                    self.assert_blocked()
                plan.assert_not_called()

    def test_legacy_completed_and_prejournal_backups_do_not_block(self):
        for name, state in [('old', 'applied'), ('reverted', 'rolled_back'), ('cancel', 'cancelled')]:
            backup = self.backups / ('update-' + name)
            backup.mkdir(parents=True)
            (backup / 'manifest.json').write_text(json.dumps({'state': state}))
        (self.backups / 'update-no-journal').mkdir()
        self.assertEqual(self.apply()['state'], 'applied')


if __name__ == '__main__':
    unittest.main()
