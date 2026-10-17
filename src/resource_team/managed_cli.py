"""Private model-free source initialization/inspection and explicit human transitions."""

import argparse
import json

from .managed import ManagedSource
from .strict import parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True)
    init.add_argument("--fixture", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--db", required=True)
    intent = commands.add_parser("intent")
    intent.add_argument("--db", required=True)
    intent.add_argument("--request-id", required=True)
    intent.add_argument("--task", required=True)
    human = commands.add_parser("human")
    human.add_argument("--db", required=True)
    human.add_argument("--action-json", required=True)
    args = parser.parse_args(argv)
    try:
        source = (
            ManagedSource.seed(args.db, args.fixture)
            if args.command == "init"
            else ManagedSource(args.db)
        )
        try:
            if args.command == "intent":
                result = source.intent(args.request_id, args.task)
            elif args.command == "human":
                result = source.human(
                    parse_json(args.action_json.encode("utf-8", errors="strict"))
                )
            else:
                result = source.snapshot()
        finally:
            cleanup = source.close()
            if not cleanup["database_closed"] or not cleanup["descriptor_closed"]:
                raise RuntimeError("private allocation source cleanup unconfirmed")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
