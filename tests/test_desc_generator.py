import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama import config
from mama import desc_generator
from mama.cli_commands import cmd_new_os_desc
from mama.cli_parser import setup_cli_parser


class TestDescGenerator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_mama_path = config.MAMA_PATH
        config.MAMA_PATH = self.temp_dir.name

    def tearDown(self):
        config.MAMA_PATH = self.orig_mama_path
        self.temp_dir.cleanup()

    def test_probe_opensuse_leap_versions_success(self):
        html_sample = """
        <html><body>
        <a href="./15.4/">15.4/</a>
        <a href="./15.5/">15.5/</a>
        <a href="./15.6/">15.6/</a>
        <a href="./16.0/">16.0/</a>
        <a href="./16.1/">16.1/</a>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_sample.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            versions = desc_generator.probe_opensuse_leap_versions()
            self.assertEqual(versions[0], "16.1")
            self.assertEqual(versions[1], "16.0")
            self.assertIn("15.6", versions)

    def test_probe_opensuse_leap_versions_fallback(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            versions = desc_generator.probe_opensuse_leap_versions()
            self.assertEqual(versions, desc_generator.FALLBACK_LEAP_VERSIONS)

    def test_build_repositories_leap_15_6_with_kotd(self):
        repos = desc_generator.build_repositories(
            distro_family="opensuse",
            version="15.6",
            arch="x86_64",
            kernel_flavor="kotd",
        )
        aliases = [r["alias"] for r in repos]
        self.assertIn("Leap_15_6", aliases)
        self.assertIn("Leap_15_6_Update_OSS", aliases)
        self.assertIn("repo-sle-update", aliases)
        self.assertIn("repo-backports-update", aliases)
        self.assertIn("kernel", aliases)

        # Check KOTD repo URL
        kernel_repo = next(r for r in repos if r["alias"] == "kernel")
        self.assertIn("Kernel:/SLE15-SP6", kernel_repo["source"])

    def test_build_repositories_leap_16_1_with_kotd(self):
        repos = desc_generator.build_repositories(
            distro_family="opensuse",
            version="16.1",
            arch="x86_64",
            kernel_flavor="kotd",
        )
        aliases = [r["alias"] for r in repos]
        self.assertIn("Leap_16_1", aliases)
        self.assertIn("Leap_16_1_Update", aliases)
        self.assertIn("kernel", aliases)

        kernel_repo = next(r for r in repos if r["alias"] == "kernel")
        self.assertIn("Kernel:/SL-16.1", kernel_repo["source"])

    def test_build_repositories_fedora(self):
        repos = desc_generator.build_repositories(
            distro_family="fedora",
            version="44",
            arch="x86_64",
        )
        aliases = [r["alias"] for r in repos]
        self.assertIn("Fedora44", aliases)
        self.assertIn("Fedora44-updates", aliases)

        fedora_repo = next(r for r in repos if r["alias"] == "Fedora44")
        self.assertEqual(fedora_repo["sourcetype"], "metalink")
        self.assertIn("repo=fedora-44&arch=x86_64", fedora_repo["source"])

    def test_packages_arch_differences(self):
        pkgs_x86, _ = desc_generator.get_packages("opensuse", "x86_64")
        x86_names = [p["name"] for p in pkgs_x86]
        self.assertIn("grub2-x86_64-efi", x86_names)
        self.assertIn("kernel-firmware-amdgpu", x86_names)

        pkgs_arm, _ = desc_generator.get_packages("opensuse", "aarch64")
        arm_names = [p["name"] for p in pkgs_arm]
        self.assertIn("grub2-arm64-efi", arm_names)
        self.assertIn("xz", arm_names)

    def test_create_os_description_opensuse(self):
        desc_path = desc_generator.create_os_description(
            arch="x86_64",
            name="test-leap-16.1",
            distro_family="opensuse",
            version="16.1",
            kernel_flavor="kotd",
            extra_profiles=["igt"],
            base_path=self.temp_dir.name,
        )
        self.assertTrue(desc_path.is_dir())
        kiwi_file = desc_path / "appliance.kiwi"
        self.assertTrue(kiwi_file.exists())
        kiwi_content = kiwi_file.read_text(encoding="utf-8")
        self.assertIn("Leap_16_1", kiwi_content)
        self.assertIn("Leap_16_1_Update", kiwi_content)
        self.assertIn("Kernel:/SL-16.1", kiwi_content)
        self.assertIn("meson", kiwi_content)
        self.assertIn("libdrm-devel", kiwi_content)
        self.assertIn('<package name="grep"/>', kiwi_content)
        self.assertIn('<package name="gawk"/>', kiwi_content)
        self.assertIn('<package name="diffutils"/>', kiwi_content)
        self.assertIn('<package name="gzip"/>', kiwi_content)
        self.assertIn(desc_generator.DEFAULT_ROOT_PASSWORD_HASH, kiwi_content)

        config_sh = desc_path / "config.sh"
        self.assertTrue(config_sh.exists())
        self.assertTrue(os.access(str(config_sh), os.X_OK))
        self.assertIn("suseSetupProduct", config_sh.read_text(encoding="utf-8"))

        self.assertTrue((desc_path / "root").is_dir())

        # Attempting duplicate creation raises FileExistsError
        with self.assertRaises(FileExistsError):
            desc_generator.create_os_description(
                arch="x86_64",
                name="test-leap-16.1",
                distro_family="opensuse",
                version="16.1",
                base_path=self.temp_dir.name,
            )

    def test_create_os_description_fedora(self):
        desc_path = desc_generator.create_os_description(
            arch="x86_64",
            name="test-fedora-44",
            distro_family="fedora",
            version="44",
            base_path=self.temp_dir.name,
        )
        self.assertTrue(desc_path.is_dir())
        kiwi_file = desc_path / "appliance.kiwi"
        kiwi_content = kiwi_file.read_text(encoding="utf-8")
        self.assertIn("dnf5", kiwi_content)
        self.assertIn("Fedora44", kiwi_content)
        self.assertIn("Fedora44-updates", kiwi_content)

        config_sh = desc_path / "config.sh"
        self.assertIn("dbus-broker", config_sh.read_text(encoding="utf-8"))
        self.assertTrue((desc_path / "root" / "fastboot").exists())

    def test_cmd_new_os_desc_non_interactive(self):
        cmd_new_os_desc(
            arch="x86_64",
            distro="opensuse",
            version="16.1",
            kernel="kotd",
            name="auto-leap-16.1-kotd",
            profiles=["baseline"],
        )
        expected_dir = pathlib.Path(self.temp_dir.name) / "os-descriptions" / "x86_64" / "auto-leap-16.1-kotd"
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue((expected_dir / "appliance.kiwi").exists())

    def test_cli_parser_new_os_desc(self):
        parser = setup_cli_parser()
        args = parser.parse_args([
            "new-os-desc",
            "--arch", "x86_64",
            "--distro", "opensuse",
            "--version", "16.1",
            "--kernel", "kotd",
            "--name", "cli-test-desc",
        ])
        self.assertEqual(args.command, "new-os-desc")
        self.assertEqual(args.arch, "x86_64")
        self.assertEqual(args.distro, "opensuse")
        self.assertEqual(args.version, "16.1")
        self.assertEqual(args.kernel, "kotd")
        self.assertEqual(args.name, "cli-test-desc")

    @patch("questionary.checkbox")
    @patch("questionary.text")
    @patch("questionary.select")
    def test_cmd_new_os_desc_interactive_when_no_existing_descriptions(
        self, mock_select, mock_text, mock_checkbox
    ):
        mock_select.return_value.ask.side_effect = [
            "x86_64",
            "openSUSE",
            "Leap 16.1",
            "Stock (Distribution default)",
        ]
        mock_text.return_value.ask.return_value = "interactive-leap-16.1"
        mock_checkbox.return_value.ask.return_value = ["baseline"]

        cmd_new_os_desc()

        expected_dir = pathlib.Path(self.temp_dir.name) / "os-descriptions" / "x86_64" / "interactive-leap-16.1"
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue((expected_dir / "appliance.kiwi").exists())


if __name__ == "__main__":
    unittest.main()
