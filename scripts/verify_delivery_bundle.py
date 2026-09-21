"""只读验证 Delivery Bundle Manifest 与 Product Root。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_team_os.delivery_bundle import verify_delivery_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_root", type=Path)
    arguments = parser.parse_args()
    result = verify_delivery_bundle(arguments.bundle_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("deterministic_gate=not_run; live_gate=not_run; apply=not_authorized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
