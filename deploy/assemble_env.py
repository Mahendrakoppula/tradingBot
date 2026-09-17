"""Append SSM parameters (JSON on stdin: [{"Name","Value"}, ...]) to the
.env file named by argv[1]. Called by deploy/fetch_secrets.sh at boot.

Only names that are valid environment-variable identifiers are written.
systemd rejects anything else (e.g. the review agent's dash-named
/trading-bot/anthropic-api-key and /trading-bot/github-token) and, worse,
LOGS THE FULL VALUE in journald as "Ignoring invalid environment
assignment" on every unit start - a secret leak. Those parameters are read
directly by deploy/setup_claude_agent.sh and never belonged in .env.

Values are double-quoted with backslash/quote escaping, which both systemd
EnvironmentFile= and python-dotenv parse identically, so a value containing
spaces or '#' can't truncate or comment out the line.
"""
import json
import re
import sys

VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render(params: list[dict]) -> tuple[list[str], list[str]]:
    """Returns (lines to append, names skipped)."""
    lines, skipped = [], []
    for p in params:
        key = p["Name"].rsplit("/", 1)[-1]
        if not VALID_NAME.match(key):
            skipped.append(p["Name"])
            continue
        value = str(p["Value"]).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{value}"')
    return lines, skipped


def main() -> int:
    env_file = sys.argv[1]
    params = json.load(sys.stdin)
    lines, skipped = render(params)
    with open(env_file, "a", encoding="utf-8") as f:
        f.write("\n# --- secrets, fetched from SSM Parameter Store at boot ---\n")
        for line in lines:
            f.write(line + "\n")
    for name in skipped:
        # name only - never the value
        print(f"skipping SSM parameter {name}: not a valid environment variable name", file=sys.stderr)
    print(f"wrote {len(lines)} parameters, skipped {len(skipped)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
