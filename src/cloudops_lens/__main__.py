from __future__ import annotations

import argparse
import json

from cloudops_lens.capacity import CapacityUnavailable, refresh_private_capacity
from cloudops_lens.config import DEFAULT_DB_PATH
from cloudops_lens.pipeline import build_database
from cloudops_lens.refresh import refresh_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudOps Lens data pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("refresh", help="Fetch and atomically validate a current source snapshot")
    subparsers.add_parser(
        "refresh-capacity",
        help="Privately snapshot authenticated Lambda regional capacity",
    )
    build_parser = subparsers.add_parser(
        "build", help="Build DuckDB from the latest local snapshot"
    )
    build_parser.add_argument("--output", default=str(DEFAULT_DB_PATH), help="DuckDB output path")
    args = parser.parse_args()

    if args.command == "refresh":
        result = refresh_sources()
        printable = {**result, "snapshot": result["snapshot"].snapshot_id}
        print(json.dumps(printable, indent=2))
    elif args.command == "refresh-capacity":
        try:
            print(json.dumps(refresh_private_capacity(), indent=2))
        except CapacityUnavailable as error:
            parser.error(str(error))
    elif args.command == "build":
        summary = build_database(args.output)
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
