"""Print an argon2 hash of the owner password, for OWNER_PASSWORD_HASH.

    make owner-hash

The password is read without echo and never written anywhere; only the hash is printed. Paste the
hash into .env (local) or the host's environment settings (staging, production).
"""

import getpass
import sys

from services.api.auth.passwords import MAX_PASSWORD_LENGTH
from services.api.auth.passwords import hash_password as derive_hash

MIN_LENGTH = 12


def main() -> int:
    typed = getpass.getpass("Owner password: ")
    if getpass.getpass("Again: ") != typed:
        print("The two entries differ. Nothing was printed.", file=sys.stderr)
        return 1
    if not MIN_LENGTH <= len(typed) <= MAX_PASSWORD_LENGTH:
        print(f"Use between {MIN_LENGTH} and {MAX_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1
    argon2_hash = derive_hash(typed)
    sys.stdout.write(argon2_hash + "\n")  # the hash is the intended output, not the password
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
