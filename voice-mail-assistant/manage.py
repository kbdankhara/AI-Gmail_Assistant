"""Small admin commands.

    python manage.py generate-key    print a new ENCRYPTION_KEY and SECRET_KEY
    python manage.py create-plan     create the monthly plan in Razorpay and print its id
"""
import secrets
import sys

from cryptography.fernet import Fernet


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "generate-key":
        print("SECRET_KEY=" + secrets.token_hex(32))
        print("ENCRYPTION_KEY=" + Fernet.generate_key().decode())
        return 0
    if command == "create-plan":
        from app import billing
        from app.config import Config
        cfg = Config()
        if not (cfg.RAZORPAY_KEY_ID and cfg.RAZORPAY_KEY_SECRET):
            print("Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET first.")
            return 1
        plan = billing.create_plan(cfg)
        print("Created. Add this to your .env:")
        print("RAZORPAY_PLAN_ID=" + plan["id"])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
