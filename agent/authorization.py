"""Client-certificate authorization helpers for the machine agent."""

import hashlib


class AuthorizationError(PermissionError):
    """Raised when a client certificate is not authorized for this machine."""


VALID_ROLES = {"monitor", "operator", "technician", "administrator"}


def certificate_fingerprint(peer_certificate_der):
    """Return the normalized SHA-256 fingerprint of a peer certificate."""
    if not peer_certificate_der:
        raise AuthorizationError("A client certificate is required.")
    return hashlib.sha256(peer_certificate_der).hexdigest().upper()


def authorize_client(peer_certificate_der, allowed_fingerprints):
    """Authorize a peer certificate against a configured fingerprint allowlist."""
    fingerprint = certificate_fingerprint(peer_certificate_der)
    allowed = {value.replace(":", "").replace(" ", "").strip().upper() for value in allowed_fingerprints}
    if allowed and fingerprint not in allowed:
        raise AuthorizationError("Client certificate is not authorized for this machine.")
    return fingerprint


def authorize_role(fingerprint, required_role, client_roles):
    """Require a role for an operation when role mappings are configured."""
    if not client_roles:
        return
    roles = set(client_roles.get(fingerprint, []))
    if required_role not in roles and "administrator" not in roles:
        raise AuthorizationError(f"Client lacks the required {required_role} role.")


def parse_role_mapping(entries):
    """Parse CLI entries formatted as ``FINGERPRINT=role,role``."""
    mappings = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError("Client role must use FINGERPRINT=role,role format.")
        fingerprint, roles_text = entry.split("=", 1)
        normalized = fingerprint.replace(":", "").replace(" ", "").strip().upper()
        roles = {role.strip().lower() for role in roles_text.split(",") if role.strip()}
        if len(normalized) != 64 or any(character not in "0123456789ABCDEF" for character in normalized):
            raise ValueError("Client role fingerprint is invalid.")
        if not roles or not roles.issubset(VALID_ROLES):
            raise ValueError("Client role is invalid.")
        mappings[normalized] = sorted(roles)
    return mappings