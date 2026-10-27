import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backend


class TestMilestone4PackagingAndDesktop(unittest.TestCase):
    """Verifies native packaging, desktop entries, icon assets, dependency checks, and installation."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.desktop_file = os.path.join(cls.repo_root, "desktop", "videoclipper.desktop")
        cls.svg_icon = os.path.join(cls.repo_root, "assets", "videoclipper.svg")
        cls.png_icon = os.path.join(cls.repo_root, "assets", "videoclipper.png")
        cls.pkgbuild_file = os.path.join(cls.repo_root, "packaging", "PKGBUILD")
        cls.install_script = os.path.join(cls.repo_root, "packaging", "install.sh")

    def test_desktop_file_exists_and_passes_desktop_file_validate(self):
        self.assertTrue(os.path.exists(self.desktop_file), "Desktop file missing")
        res = subprocess.run(["desktop-file-validate", self.desktop_file], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"desktop-file-validate failed: {res.stderr}")

    def test_desktop_entry_spec_fields(self):
        with open(self.desktop_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Type=Application", content)
        self.assertIn("Name=VideoClipper", content)
        self.assertIn("Exec=videoclipper %F", content)
        self.assertIn("Icon=videoclipper", content)
        self.assertIn("MimeType=", content)
        self.assertIn("video/mp4;", content)
        self.assertIn("video/x-matroska;", content)
        self.assertIn("Categories=", content)
        self.assertIn("AudioVideo;", content)

    def test_icon_assets_exist_and_valid(self):
        self.assertTrue(os.path.exists(self.svg_icon), "SVG icon missing")
        self.assertTrue(os.path.exists(self.png_icon), "PNG icon missing")
        # Check size of icons
        self.assertGreater(os.path.getsize(self.svg_icon), 100)
        self.assertGreater(os.path.getsize(self.png_icon), 1000)

    def test_pkgbuild_declares_exact_dependencies(self):
        self.assertTrue(os.path.exists(self.pkgbuild_file), "PKGBUILD missing")
        with open(self.pkgbuild_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pkgname=videoclipper", content)
        self.assertIn("depends=('python' 'python-pyqt6' 'python-opencv' 'python-numpy' 'ffmpeg')", content)
        self.assertIn("usr/bin/videoclipper", content)
        self.assertIn("usr/lib/videoclipper", content)
        self.assertIn("videoclipper.desktop", content)

    def test_external_dependency_check(self):
        # When ffmpeg & ffprobe are present, backend.check_external_dependencies returns True
        self.assertTrue(backend.check_external_dependencies())

    def test_default_output_dir_is_user_owned(self):
        out_dir = backend.get_default_output_dir()
        self.assertTrue(os.path.isabs(out_dir))
        # Ensure it does not point into /usr or system directories
        self.assertFalse(out_dir.startswith("/usr"), f"Output dir must not be in /usr: {out_dir}")
        self.assertFalse(out_dir.startswith("/opt"), f"Output dir must not be in /opt: {out_dir}")
        # Must be writable
        test_file = os.path.join(out_dir, f".write_test_{os.getpid()}")
        with open(test_file, "w") as f:
            f.write("test")
        self.assertTrue(os.path.exists(test_file))
        os.remove(test_file)

    def test_standalone_prefix_installation_and_uninstallation(self):
        """Tests end-to-end installation into an isolated prefix, verifying all files and paths."""
        with tempfile.TemporaryDirectory(prefix="vc_install_test_") as prefix:
            # 1. Run install
            res_inst = subprocess.run([
                self.install_script, prefix, "install"
            ], capture_output=True, text=True)
            self.assertEqual(res_inst.returncode, 0, f"Install script failed: {res_inst.stderr}")

            installed_bin = os.path.join(prefix, "bin", "videoclipper")
            installed_backend = os.path.join(prefix, "lib", "videoclipper", "backend.py")
            installed_main = os.path.join(prefix, "lib", "videoclipper", "main.py")
            installed_desktop = os.path.join(prefix, "share", "applications", "videoclipper.desktop")
            installed_svg = os.path.join(prefix, "share", "icons", "hicolor", "scalable", "apps", "videoclipper.svg")
            installed_png = os.path.join(prefix, "share", "icons", "hicolor", "256x256", "apps", "videoclipper.png")

            self.assertTrue(os.path.isfile(installed_bin))
            self.assertTrue(os.access(installed_bin, os.X_OK))
            self.assertTrue(os.path.isfile(installed_backend))
            self.assertTrue(os.path.isfile(installed_main))
            self.assertTrue(os.path.isfile(installed_desktop))
            self.assertTrue(os.path.isfile(installed_svg))
            self.assertTrue(os.path.isfile(installed_png))

            # 2. Verify installed files run from an external working directory
            with tempfile.TemporaryDirectory() as foreign_cwd:
                # Run python py_compile on installed files from foreign cwd
                res_compile = subprocess.run(
                    [sys.executable, "-m", "py_compile", installed_backend, installed_main],
                    cwd=foreign_cwd, capture_output=True, text=True
                )
                self.assertEqual(res_compile.returncode, 0, f"Installed scripts failed compile check: {res_compile.stderr}")

                # Test executing backend from installed path
                res_backend = subprocess.run(
                    [sys.executable, "-c", f"import sys; sys.path.insert(0, '{os.path.dirname(installed_backend)}'); import backend; print(backend.get_default_output_dir())"],
                    cwd=foreign_cwd, capture_output=True, text=True
                )
                self.assertEqual(res_backend.returncode, 0, f"Installed backend import failed: {res_backend.stderr}")

            # 3. Run uninstall
            res_uninst = subprocess.run([
                self.install_script, prefix, "uninstall"
            ], capture_output=True, text=True)
            self.assertEqual(res_uninst.returncode, 0, f"Uninstall script failed: {res_uninst.stderr}")

            self.assertFalse(os.path.exists(installed_bin))
            self.assertFalse(os.path.exists(installed_backend))
            self.assertFalse(os.path.exists(installed_main))
            self.assertFalse(os.path.exists(installed_desktop))
            self.assertFalse(os.path.exists(installed_svg))
            self.assertFalse(os.path.exists(installed_png))


if __name__ == "__main__":
    unittest.main()
