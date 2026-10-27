import unittest
import tempfile
import getpass
from pathlib import Path

from core.credentials import CredentialStore, CredentialStoreError
from settings_manager import SettingsManager


class FakeKeyring:
    """In-memory keyring double for testing the credential boundary."""

    def __init__(self):
        self.values = {}

    def set_password(self, service, account, secret):
        self.values[(service, account)] = secret

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class CredentialStoreTests(unittest.TestCase):
    def test_round_trip_uses_keyring_backend(self):
        store = CredentialStore(keyring_module=FakeKeyring())

        store.save("machine-user", "secret")

        self.assertEqual(store.get("machine-user"), "secret")
        store.delete("machine-user")
        self.assertIsNone(store.get("machine-user"))

    def test_rejects_missing_account_or_non_string_secret(self):
        store = CredentialStore(keyring_module=FakeKeyring())

        with self.assertRaises(CredentialStoreError):
            store.save("", "secret")
        with self.assertRaises(CredentialStoreError):
            store.save("machine-user", None)

    def test_settings_manager_migrates_legacy_ssh_password(self):
        keyring = FakeKeyring()
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            settings_path.write_text("[SSH]\npassword = legacy-secret\n", encoding="utf-8")

            settings = SettingsManager(settings_path, CredentialStore(keyring_module=keyring))

            self.assertEqual(settings.get_setting("SSH", "password"), "legacy-secret")
            self.assertNotIn("legacy-secret", settings_path.read_text(encoding="utf-8"))

    def test_settings_manager_writes_ssh_password_only_to_keyring(self):
        keyring = FakeKeyring()
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            settings = SettingsManager(settings_path, CredentialStore(keyring_module=keyring))

            settings.save_setting("SSH", "password", "new-secret")

            self.assertEqual(settings.get_setting("SSH", "password"), "new-secret")
            self.assertNotIn("new-secret", settings_path.read_text(encoding="utf-8"))

    def test_local_only_settings_uses_loopback_and_ignores_remote_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            settings_path.write_text(
                "[SSH]\nip_address = 192.0.2.10\nusername = remote-user\npassword = secret\n"
                "[AGENT]\nbase_url = https://192.0.2.10:9443\n",
                encoding="utf-8",
            )

            settings = SettingsManager(settings_path, local_only=True)

            self.assertEqual(settings.get_setting("SSH", "ip_address"), "127.0.0.1")
            self.assertEqual(settings.get_setting("SSH", "username"), getpass.getuser())
            self.assertEqual(settings.get_setting("SSH", "password"), "")
            self.assertEqual(settings.get_setting("AGENT", "base_url", ""), "")
            self.assertEqual(settings.get_setting("MESA", "ip_address", "10.10.10.10"), "10.10.10.10")

    def test_local_only_settings_reject_remote_connection_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsManager(Path(directory) / "settings.ini", local_only=True)

            with self.assertRaisesRegex(RuntimeError, "disabled in local-only mode"):
                settings.save_setting("SSH", "ip_address", "192.0.2.10")
            with self.assertRaisesRegex(RuntimeError, "disabled in local-only mode"):
                settings.save_setting("AGENT", "base_url", "https://192.0.2.10:9443")


if __name__ == "__main__":
    unittest.main()