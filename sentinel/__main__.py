"""Collect one explicit range; scheduling and production node selection belong to operators."""

import argparse
import json
import os
import sqlite3
import sys

from .collector import Collector
from .rpc import IngestionError, Rpc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--start-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    args = parser.parse_args()
    collector = None
    try:
        endpoint = os.environ.get("SENTINEL_RPC_URL", "")
        collector = Collector(
            args.database,
            Rpc(endpoint),
            chain_id=args.chain_id,
            start_block=args.start_block,
        )
        print(json.dumps(collector.sync(to_block=args.to_block), sort_keys=True))
        return 0
    except IngestionError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
        return 1
    except (sqlite3.Error, OSError, ValueError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "database, transport or argument failure",
                }
            )
        )
        return 1
    finally:
        if collector is not None:
            collector.close()


if __name__ == "__main__":
    sys.exit(main())
