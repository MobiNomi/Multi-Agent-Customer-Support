"""Create a staff account for the support console at /staff.

    python -m scripts.create_staff hina@acmecloud.com "Hina Raza"             # asks for the password (hidden)
    python -m scripts.create_staff hina@acmecloud.com "Hina Raza" --generate  # random password, printed once

Staff accounts can approve refunds and answer tickets. They can't use the customer chat.
"""
import argparse
import getpass
import secrets

from app.auth.passwords import MIN_PASSWORD_LENGTH
from app.auth.service import EmailTaken, create_staff
from app.database.connection import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a staff account for the support console.")
    parser.add_argument("email")
    parser.add_argument("name")
    parser.add_argument("--generate", action="store_true", help="generate a random password and print it")
    args = parser.parse_args()

    init_db()
    if args.generate:
        password = secrets.token_urlsafe(12)
    else:
        password = getpass.getpass(f"Password for {args.email} (min {MIN_PASSWORD_LENGTH} characters): ")
        if getpass.getpass("Repeat it: ") != password:
            print("The two passwords don't match. Nothing was created.")
            return 1
    try:
        staff_id = create_staff(args.name, args.email, password)
    except EmailTaken:
        print(f"An account with {args.email} already exists.")
        return 1
    except ValueError as exc:
        print(exc)
        return 1
    print(f"Staff account {staff_id} created for {args.name} <{args.email.strip().lower()}>. Sign in at /staff.")
    if args.generate:
        print(f"Password: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
