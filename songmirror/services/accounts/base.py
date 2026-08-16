"""Connector protocol — how the UI connects each service.

The engine knows how to *use* a service once tokens exist; connectors know how
to *obtain* them. One connector per service, keyed to the targets registry, so
"connect any service" is uniform in the wizard regardless of auth style.

Connectors are account-scoped: each instance is bound to ONE account id (or
None for the legacy shared path) and reads/writes ONLY that account's config
namespace. A named account never inherits the default account's credentials
from os.environ and never overwrites another account's token/cookie files.
"""

import os
from dataclasses import dataclass
from typing import Literal

AuthKind = Literal["oauth_redirect", "oauth_device", "token_paste", "api_key"]


@dataclass
class Field:
    """One input the wizard renders for a connector's app config."""

    key: str
    label: str
    secret: bool = False
    help: str = ""
    required: bool = True


@dataclass
class ConnStatus:
    state: Literal["connected", "expired", "unconfigured", "error"]
    detail: str = ""


@dataclass
class DeviceCode:
    """What the browser shows during an OAuth device flow."""

    user_code: str
    verification_url: str
    device_code: str
    interval: int = 5


class Connector:
    """Base class. Each service implements status() plus the methods for its
    auth_kind. Takes a SettingsStore so every connector reads/writes config the
    same way."""

    id: str = ""
    name: str = ""
    auth_kind: AuthKind = "api_key"
    config_fields: list[Field] = []

    def __init__(self, store, account_id: str | None = None):
        self._store = store
        # The account this connector instance manages: `{provider}:default` or a
        # named profile. None keeps the legacy shared path (flat settings keys +
        # process env).
        self._account_id = account_id

    # -- per-account config helpers --------------------------------------------
    def _config(self) -> dict:
        """The account's effective config snapshot (named accounts get their own
        session/token file paths materialized here); empty on the legacy path.
        Never reads os.environ for a named account, so two accounts of the same
        provider can't inherit each other's credentials."""
        if not self._account_id:
            return {}
        return self._store.account_config_snapshot(self._account_id)

    def _get(self, key, default=None):
        """One credential for this connector's account. Account-scoped: the
        account's own config only. Legacy (no account): the stored flat key,
        then the process env (Docker precedence)."""
        if self._account_id:
            return self._config().get(key, default)
        return self._store.get(key, default) or os.getenv(key, default)

    def _save(self, values):
        """Persist credentials for this connector's account. Named accounts land
        only in their own registry namespace; `:default` also mirrors the flat
        keys so legacy env consumers keep working. Secrets are written via the
        store's 0600 settings.json."""
        values = {k: v for k, v in values.items() if v is not None}
        if not values:
            return
        if self._account_id:
            self._store.save_account(self._account_id, config=values)
            if self._account_id.endswith(":default"):
                self._store.save(values)  # legacy flat-key consumers (CLI/env)
        else:
            self._store.save(values)

    def status(self) -> ConnStatus:
        raise NotImplementedError

    # oauth_redirect
    def begin_redirect(self, redirect_uri: str) -> str:
        raise NotImplementedError

    def complete_redirect(self, params: dict) -> ConnStatus:
        raise NotImplementedError

    # oauth_device
    def begin_device(self) -> DeviceCode:
        raise NotImplementedError

    def poll_device(self, dc: DeviceCode) -> ConnStatus:
        raise NotImplementedError

    # token_paste / api_key
    def submit(self, values: dict) -> ConnStatus:
        raise NotImplementedError

    def disconnect(self) -> None:
        """Clear connector-managed settings; providers may override for extras."""

        self._save({field.key: "" for field in self.config_fields})

    def _configured(self, *keys) -> bool:
        return all(self._get(k) for k in keys)
