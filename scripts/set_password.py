"""Give a customer a password so they can sign in.

    python -m scripts.set_password ali@example.com            # asks for the password (hidden)
    python -m scripts.set_password ali@example.com --generate # creates a random one and prints it

Setting a new password also signs the customer out on every device.
"""
import argparse
import getpass
import secrets
import sys

from app.auth.passwords import MIN_PASSWORD_LENGTH
from app.auth.service import set_password
from app.database.connection import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a customer's sign-in password.")
    parser.add_argument("email")
    parser.add_argument("--generate", action="store_true", help="generate a random password and print it")
    args = parser.parse_args()

    init_db()
    if args.generate:
        password = secrets.token_urlsafe(12)
    else:
        password = getpass.getpass(f"New password for {args.email} (min {MIN_PASSWORD_LENGTH} characters): ")
        if getpass.getpass("Repeat it: ") != password:
            print("The two passwords don't match. Nothing was changed.")
            return 1
    try:
        customer_id = set_password(args.email, password)
    except (ValueError, LookupError) as exc:
        print(exc)
        return 1
    print(f"Password set for {args.email} ({customer_id}).")
    if args.generate:
        print(f"Password: {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
