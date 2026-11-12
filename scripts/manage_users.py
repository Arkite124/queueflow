"""Provision invited users from the local administrator terminal; no public signup."""
import argparse
import getpass

from sqlalchemy import delete, select

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.models import LoginSession, User
from backend.app.services.auth import password_hash, provision_user


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "disable", "reset-password"])
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true", help="Administrator role for a NEW account")
    args = parser.parse_args()
    settings = Settings()
    sessions = make_sessions(make_engine(settings.database_url))
    if args.action == "create":
        password = getpass.getpass("New password (12–128 characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords differ")
        user_id = provision_user(sessions, args.username, password, args.admin)
        print(f"Invited account created: {args.username} ({user_id})")
    else:
        encoded = None
        if args.action == "reset-password":
            password = getpass.getpass("New password (12–128 characters): ")
            if password != getpass.getpass("Repeat password: "):
                raise SystemExit("Passwords differ")
            encoded = password_hash(password)
        with sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == args.username.lower()).with_for_update())
            if not user:
                raise SystemExit("Account not found")
            if encoded:
                user.password_hash, user.failed_logins, user.locked_until = encoded, 0, None
            else:
                user.active = False
            session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        print("Account updated; its sessions were revoked")


if __name__ == "__main__":
    main()
