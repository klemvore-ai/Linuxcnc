"""Private credential-store wrapper used for machine-admin secrets."""

from core.credentials import CredentialStore, CredentialStoreError


class PrivateCredentialStore(CredentialStore):
    """Explicitly scoped credential store for private machine-admin secrets."""

    def __init__(self, service_name="CNCFusionPrivate", keyring_module=None):
        super().__init__(service_name=service_name, keyring_module=keyring_module)
