"""Repository failures remain distinct from business access denials."""


class IdentityRepositoryError(Exception):
    """Unavailable store or violated persistence constraints."""
