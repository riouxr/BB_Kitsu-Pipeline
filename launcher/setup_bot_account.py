"""Run this once, yourself, after creating the bot account in Kitsu.

Stores the bot's email in launcher_config.json and its password in the OS
credential store (the same mechanism BB_core.credentials already uses for
the artist's own login) - never in a plain file, and never typed anywhere
but this terminal's own password prompt.

    python setup_bot_account.py bot@yourstudio.local
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import launcher_config
from BB_core import credentials

def main():
    if len(sys.argv) != 2:
        print("usage: python setup_bot_account.py <bot-email>")
        raise SystemExit(1)

    email = sys.argv[1]
    if not credentials.available():
        print("no OS credential store available on this machine")
        raise SystemExit(1)

    password = getpass.getpass("Password for %s: " % email)
    if not password:
        print("no password entered, nothing stored")
        raise SystemExit(1)

    if not credentials.set_password(email, password):
        print("failed to store the password")
        raise SystemExit(1)

    launcher_config.set("bot_email", email)
    print("stored - bb_launch_server.py will use %s from now on" % email)


if __name__ == "__main__":
    main()
