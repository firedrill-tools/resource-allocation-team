import argparse
import json
from pathlib import Path

from .agent import run_team_sync
from .store import AllocationStore


def main():
    parser = argparse.ArgumentParser(description="Synthetic shared resource allocation team")
    parser.add_argument("--db", default="workspace/resources.db")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument("--task")
    parser.add_argument("--model")
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    if args.seed:
        print(AllocationStore.seed(args.db, Path(__file__).parents[2] / "fixtures" / "starter.json").path)
        return
    if not args.request_id or not args.task:
        parser.error("--request-id and --task required")
    try:
        print(json.dumps(run_team_sync(AllocationStore(args.db), args.request_id, args.task,
                                       args.allow_writes, args.model), indent=2))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
