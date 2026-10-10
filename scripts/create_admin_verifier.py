"""Generate a server-only PBKDF2 verifier without accepting CLI plaintext."""

from getpass import getpass
import secrets

from stockpilot.presentation.api.admin_auth import password_verifier


def main() -> None:
    password = getpass("Admin password (16..256 characters, distinct from server secrets): ")
    confirmation = getpass("Repeat password: ")
    if not 16 <= len(password) <= 256 or not secrets.compare_digest(password.encode(), confirmation.encode()):
        raise SystemExit("Invalid password or confirmation.")
    print(password_verifier(password))


if __name__ == "__main__":
    main()
