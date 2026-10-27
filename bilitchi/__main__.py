"""Local sandbox demonstration only; no payment-provider connection."""
import argparse
import json
import time
from pathlib import Path

from .core import Core


def main():
    parser = argparse.ArgumentParser(description="Bilitchi reference core")
    parser.add_argument("command", choices=["init", "demo", "inventory"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--show-id", default="sample-show")
    args = parser.parse_args()
    database = Path(args.db)
    database.parent.mkdir(parents=True, exist_ok=True)
    core = Core(database)
    if args.command == "init":
        core.initialize()
        print(json.dumps({"initialized": str(database), "production_enabled": False}))
    elif args.command == "inventory":
        print(json.dumps(core.inventory(args.show_id), ensure_ascii=False, indent=2))
    else:
        if database.exists():
            parser.error("Demo requires a NEW database path; existing data is never reset.")
        core.initialize()
        now = int(time.time())
        core.add_center("sample-center", name="")
        core.add_channel("sample-web", name="")
        core.add_show("sample-show", "sample-center", ["A1", "A2", "A3"],
                      starts_at=now + 3600, sales_close_at=now + 3500,
                      unit_price=1_000_000, title="")
        hold = core.reserve("sample-web", "demo-hold", "sample-show",
                            "synthetic-buyer", ["A1", "A2"])
        order = core.create_order("sample-web", "demo-order", hold["hold_id"])
        core.record_payment(order["order_id"], "sandbox-only", "synthetic-reference",
                            order["amount"], "succeeded", "sandbox-adapter")
        result = core.order(order["order_id"])
        result["sandbox_only"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
