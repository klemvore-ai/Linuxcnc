"""Secure credential storage boundary for desktop and future agent enrollment."""

import importlib


class CredentialStoreError(RuntimeError):
    """Raised when a credential cannot be stored or retrieved securely."""


class CredentialStore:
    """Store secrets through the operating system's configured keyring."""

    def __init__(self, service_name="LinuxCNC", keyring_module=None):
        """Create a store using an injected or optional keyring implementation."""
        self.service_name = service_name
        self.keyring = keyring_module
        if self.keyring is None:
            try:
                self.keyring = importlib.import_module("keyring")
            except ImportError as error:
                raise CredentialStoreError(
                    "Secure credential storage requires the keyring package."
                ) from error

    def save(self, account, secret):
        """Save one secret under a service/account identifier."""
        if not account or not isinstance(secret, str):
            raise CredentialStoreError("A credential account and secret are required.")
        try:
            self.keyring.set_password(self.service_name, account, secret)
        except Exception as error:
            raise CredentialStoreError("The operating-system credential store rejected the secret.") from error

    def get(self, account):
        """Retrieve one secret or return ``None`` when it is not enrolled."""
        if not account:
            return None
        try:
            return self.keyring.get_password(self.service_name, account)
        except Exception as error:
            raise CredentialStoreError("The operating-system credential store could not be read.") from error

    def delete(self, account):
        """Remove one secret from the operating-system credential store."""
        if not account:
            return
        try:
            self.keyring.delete_password(self.service_name, account)
        except Exception as error:
            raise CredentialStoreError("The operating-system credential store rejected deletion.") from error