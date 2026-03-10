from __future__ import annotations

import json
import logging
import sys
from typing import Any

from dotenv import load_dotenv

from app import VkBrowserPoster, load_settings


def build_poster() -> VkBrowserPoster:
    load_dotenv()
    settings = load_settings()
    return VkBrowserPoster(settings)


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {"args": []}
    return json.loads(raw)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Missing VK worker operation"}))
        return 2

    operation = sys.argv[1].strip().lower()
    payload = read_payload()
    args = payload.get("args")
    if not isinstance(args, list):
        print(json.dumps({"ok": False, "error": "Worker payload must contain args list"}))
        return 2

    poster = build_poster()
    try:
        if operation == "post":
            if len(args) != 2:
                raise RuntimeError("VK post expects 2 arguments")
            result = poster.post(str(args[0]), list(args[1]))
        elif operation == "edit":
            if len(args) != 3:
                raise RuntimeError("VK edit expects 3 arguments")
            result = poster.edit_post(int(args[0]), str(args[1]), list(args[2]))
        elif operation == "check_session":
            if args:
                raise RuntimeError("VK session check expects no arguments")
            result = poster.check_session()
        else:
            raise RuntimeError(f"Unsupported VK worker operation: {operation}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
