from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.database_connectors import build_registry_draft, discover_database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only schema discovery for an external financial database."
    )
    parser.add_argument(
        "--url-env",
        required=True,
        help="Environment variable containing a SQLAlchemy database URL.",
    )
    parser.add_argument("--output", help="Optional JSON report path; stdout is the default.")
    parser.add_argument(
        "--config-output",
        help="Optional unapproved external database registry draft path.",
    )
    parser.add_argument(
        "--database-id",
        default="temporary_finance_db",
        help="Safe identifier used in the generated registry draft.",
    )
    args = parser.parse_args()
    url = os.getenv(args.url_env, "").strip()
    if not url:
        parser.error(f"Environment variable {args.url_env!r} is empty")
    report = discover_database(url)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(f"Schema report written: {path}")
    else:
        print(payload)
    if args.config_output:
        draft = build_registry_draft(report, args.database_id, args.url_env)
        path = Path(args.config_output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Unapproved registry draft written: {path}")


if __name__ == "__main__":
    main()
