"""Normal owned SDK CLI with explicit caller action flags and cooperative OS signals."""

import argparse
import asyncio
import json
import signal

from .owned_runtime import run_owned_team
from .strict import parse_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--intent-json")
    parser.add_argument("--output", default="artifacts/resource-allocation")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    args = parser.parse_args()
    observed_signal = None

    async def run():
        nonlocal observed_signal
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def cancel(name):
            nonlocal observed_signal
            observed_signal = name
            stop.set()

        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        for value in signals:
            loop.add_signal_handler(value, cancel, value.name)
        try:
            return await run_owned_team(
                database=args.db,
                request_id=args.request_id,
                task=args.task,
                allow_writes=args.allow_writes,
                model=args.model,
                base_url=args.base_url,
                output_root=args.output,
                timeout_ms=args.timeout_ms,
                intent=(
                    parse_json(args.intent_json.encode("utf-8", errors="strict"))
                    if args.intent_json
                    else None
                ),
                stop_event=stop,
            )
        finally:
            for value in signals:
                loop.remove_signal_handler(value)

    try:
        print(
            json.dumps(
                asyncio.run(run()), ensure_ascii=False, allow_nan=False, indent=2
            )
        )
    except Exception as error:
        evidence = getattr(error, "evidence", {})
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "receipt_path": getattr(error, "receipt_path", None),
                    "observed_signal": observed_signal,
                    "business": evidence.get("observed_business"),
                    "acknowledged_effects": evidence.get("acknowledged_effects"),
                    "cleanup_confirmed": evidence.get("cleanup_confirmed"),
                },
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
