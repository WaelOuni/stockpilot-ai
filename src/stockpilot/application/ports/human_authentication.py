"""Trusted authentication seam. No network adapter is supplied by V13.7.1."""

from typing import Protocol

from stockpilot.domain.entities.identity import ExternalIdentity


class HumanAuthentication(Protocol):
    def authenticate(self, request_context: object) -> ExternalIdentity | None:
        """Authenticate an opaque request using trusted, server-injected code.

        Future OIDC/BFF adapter verifies credentials/session, issuer, subject,
        audience, lifetime and signature before returning a human identity key.
        Never deserialize an ExternalIdentity or 'verified' flag from a browser.
        Machine credentials must not be accepted by this human-only contract.
        """
        ...
