"""Examine the staged Git objects before making a public release; output paths only."""
from pathlib import PurePosixPath
import re
import subprocess
import sys

FORBIDDEN = {".oci", ".source", ".codex", ".agents", ".sentrux", ".local", ".terraform",
             ".venv", "graphify-out", "__pycache__", ".pytest_cache"}
PRIVATE_FILE = {".env", "config", "credentials", "tnsnames.ora", "sqlnet.ora", "id_rsa"}
EXTENSIONS = {".pem", ".key", ".zip", ".p12", ".sso", ".jks", ".pdf", ".tfstate", ".tfvars", ".log"}
PATTERNS = [
    rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    rb"\bocid1\.[a-z]+\.[a-z0-9-]*\.[a-z0-9-]*\.[a-z0-9]{25,}",
    rb"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{30,}",
    rb"(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    rb"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+[\\/]",
]


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    paths = [p.decode("utf-8") for p in git("ls-files", "--cached", "-z").split(b"\0") if p]
    if not paths:
        raise ValueError("No staged files to check")
    flagged = []
    for name in paths:
        path = PurePosixPath(name.lower())
        if FORBIDDEN.intersection(path.parts) or path.name in PRIVATE_FILE or path.suffix in EXTENSIONS:
            flagged.append(name)
            continue
        data = git("show", ":" + name)
        if any(re.search(rx, data) for rx in PATTERNS):
            flagged.append(name)
    if flagged:
        print("Review before publishing:", *flagged, sep="\n")
        return 1
    print(f"{len(paths)} staged files checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

