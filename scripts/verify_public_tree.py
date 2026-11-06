"""Inspect a committed release tree, independently of ignored files and build products."""

import argparse
import subprocess
from pathlib import PurePosixPath


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--forbid-text", action="append", default=[], help="Additional private term (case-insensitive)")
    parser.add_argument(
        "--exclude-ancestor", action="append", default=[], help="Private commit that must be unreachable"
    )
    args = parser.parse_args()
    commit = git("rev-parse", "--verify", "--end-of-options", f"{args.ref}^{{commit}}").decode().strip()
    ancestors = set(git("rev-list", commit).decode().splitlines())
    for excluded in args.exclude_ancestor:
        if git("rev-parse", "--verify", "--end-of-options", f"{excluded}^{{commit}}").decode().strip() in ancestors:
            raise SystemExit("Private commit is reachable from the proposed release.")
    terms = [term.lower().encode() for term in args.forbid_text if term]
    checked = 0
    violations = []
    for record in git("ls-tree", "-rz", commit).split(b"\0"):
        if not record:
            continue
        metadata, filename = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = PurePosixPath(filename.decode())
        private = (
            path.parts[0] in {".flow", ".codex", "logs", "test_output", ".venv", "dist"}
            or path.parts[:2] in {("docs", "baseline"), ("docs", "verification")}
            or path.name in {".env", ".mcp.json", ".DS_Store", "settings.local.json", "CLAUDE.local.md"}
            or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".dmg", ".bundle"}
        )
        if kind != "blob" or mode not in {"100644", "100755"} or private:
            violations.append(str(path))
        elif terms:
            blob = git("cat-file", "blob", oid).lower()
            if any(term in blob or term in filename.lower() for term in terms):
                violations.append(str(path))
        checked += 1
    if violations:
        raise SystemExit("Public source policy violations:\n" + "\n".join(violations))
    print(f"Public tree passed: {commit}, {checked} files.")


if __name__ == "__main__":
    main()
