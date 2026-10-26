import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from app.desktop_bridge_package import PACKAGE_FILES, build_desktop_bridge_archive


class DesktopBridgePackageTests(unittest.TestCase):
    def test_download_contains_only_tool_sources_and_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in PACKAGE_FILES:
                (root / name).write_bytes(b'line one\nline two\n')
            (root / '.env').write_text('PRIVATE_TOKEN=must-not-ship')
            (root / 'session.json').write_text('private cookies')
            (root / '.venv').mkdir()
            (root / '.venv' / 'settings.txt').write_text('private settings')
            with ZipFile(BytesIO(build_desktop_bridge_archive(root))) as archive:
                self.assertEqual(set(archive.namelist()), {
                    f'XianyuDesktopBridge/{name}' for name in PACKAGE_FILES
                })
                self.assertEqual(archive.read('XianyuDesktopBridge/start.bat'), b'line one\r\nline two\r\n')

    def test_missing_or_linked_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                build_desktop_bridge_archive(root)
            for name in PACKAGE_FILES:
                (root / name).write_text('source')
            (root / 'bridge.py').unlink()
            (root / 'bridge.py').symlink_to(root / 'app.py')
            with self.assertRaises(ValueError):
                build_desktop_bridge_archive(root)


if __name__ == '__main__':
    unittest.main()
