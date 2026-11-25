"""Manifest publication syncs complete JSON and preserves existing capture evidence."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.capture_tge_validation import write_manifest

SOURCE = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-26_recaptured/manifest.json'


class AtomicManifestTests(unittest.TestCase):
    def test_real_manifest_is_synced_before_publication_with_compatible_bytes(self):
        original = SOURCE.read_bytes()
        manifest = json.loads(original)
        events = []
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / 'manifest.json'
            def sync(fd):
                os.fsync(fd)
                events.append('fsync')
            def publish(source, destination):
                self.assertEqual(events, ['fsync'])
                self.assertFalse(target.exists())
                self.assertEqual(Path(source).read_bytes(), original.rstrip(b'\n'))
                self.assertEqual(json.loads(Path(source).read_bytes()), manifest)
                os.link(source, destination)
            with patch('tools.capture_tge_validation.fsync', side_effect=sync), patch(
                'tools.capture_tge_validation.link', side_effect=publish
            ):
                write_manifest(directory, manifest)
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                write_manifest(directory, {'entries': [], 'errors': ['replacement']})
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(directory.iterdir()), [target])
        self.assertEqual(SOURCE.read_bytes(), original)

    def test_publication_failures_and_invalid_json_leave_capture_files_unchanged(self):
        manifest = json.loads(SOURCE.read_bytes())
        for stage in ('fsync', 'link'):
            with self.subTest(stage=stage), TemporaryDirectory() as temporary:
                directory = Path(temporary)
                evidence = {'source.html.gz': b'original compressed evidence',
                            'reference.csv': b'original reference'}
                for name, content in evidence.items():
                    (directory / name).write_bytes(content)
                with patch('tools.capture_tge_validation.' + stage,
                           side_effect=OSError('injected manifest failure')):
                    with self.assertRaisesRegex(OSError, 'injected manifest failure'):
                        write_manifest(directory, manifest)
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, evidence)
                with patch('tools.capture_tge_validation.NamedTemporaryFile') as temporary_file:
                    with self.assertRaises(TypeError):
                        write_manifest(directory, {'entries': [object()], 'errors': []})
                    temporary_file.assert_not_called()
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, evidence)
                write_manifest(directory, manifest)
                self.assertEqual(json.loads((directory / 'manifest.json').read_bytes()), manifest)
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()
                                  if p.name != 'manifest.json'}, evidence)


if __name__ == '__main__':
    unittest.main()
