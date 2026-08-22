import hashlib
import unittest

from agent.authorization import AuthorizationError, authorize_client, authorize_role, certificate_fingerprint, parse_role_mapping


class AuthorizationTests(unittest.TestCase):
    def test_fingerprint_is_normalized_sha256(self):
        certificate = b"test-certificate"

        self.assertEqual(
            certificate_fingerprint(certificate),
            hashlib.sha256(certificate).hexdigest().upper(),
        )

    def test_allowlist_accepts_colon_or_plain_fingerprint(self):
        certificate = b"test-certificate"
        fingerprint = certificate_fingerprint(certificate)

        self.assertEqual(authorize_client(certificate, [fingerprint]), fingerprint)
        self.assertEqual(authorize_client(certificate, [": ".join([fingerprint[i:i + 2] for i in range(0, 64, 2)])]), fingerprint)

    def test_allowlist_rejects_unknown_client(self):
        with self.assertRaises(AuthorizationError):
            authorize_client(b"client", ["0" * 64])

    def test_empty_allowlist_preserves_authenticated_ca_compatibility(self):
        self.assertTrue(authorize_client(b"client", []))

    def test_roles_separate_operator_and_technician_operations(self):
        fingerprint = certificate_fingerprint(b"operator")
        roles = {fingerprint: ["operator"]}

        authorize_role(fingerprint, "operator", roles)
        with self.assertRaises(AuthorizationError):
            authorize_role(fingerprint, "technician", roles)

    def test_administrator_can_perform_any_role(self):
        fingerprint = certificate_fingerprint(b"administrator")

        authorize_role(fingerprint, "administrator", {fingerprint: ["administrator"]})
        authorize_role(fingerprint, "technician", {fingerprint: ["administrator"]})

    def test_parse_role_mapping_normalizes_fingerprint_and_roles(self):
        fingerprint = certificate_fingerprint(b"role-client")
        colon_fingerprint = ":".join(fingerprint[index:index + 2] for index in range(0, 64, 2))

        mapping = parse_role_mapping([f"{colon_fingerprint}=monitor,technician"])

        self.assertEqual(mapping[fingerprint], ["monitor", "technician"])
        with self.assertRaises(ValueError):
            parse_role_mapping([f"{fingerprint}=root"])


if __name__ == "__main__":
    unittest.main()