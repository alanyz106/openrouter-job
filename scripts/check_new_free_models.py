import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

CHECK_CURRENT = DATA / "check_current.json"
DAILY_CURRENT = DATA / "current.json"
API_URL = "https://openrouter.ai/api/v1/models"

WX_APP_TOKEN = os.environ.get("WX_APP_TOKEN", "")
WX_UIDS = os.environ.get("WX_UIDS", "")


def fetch_models():
    req = Request(API_URL, headers={"User-Agent": "github-actions-openrouter-check"})
    with urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return payload.get("data", payload)


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyse_pricing(pricing):
    if not isinstance(pricing, dict):
        pricing = {}

    pricing_fields = [
        "prompt", "completion", "request", "image",
        "web_search", "internal_reasoning", "input_cache_read", "input_cache_write",
    ]

    parsed = {}
    for key in pricing_fields:
        if key in pricing:
            num = to_float(pricing.get(key))
            if num is not None:
                parsed[key] = num

    has_pricing = len(parsed) > 0
    zero_fields = sorted([k for k, v in parsed.items() if v == 0])
    non_zero_fields = sorted([k for k, v in parsed.items() if v != 0])
    all_zero = has_pricing and all(v == 0 for v in parsed.values())

    return {
        "has_pricing": has_pricing,
        "parsed": parsed,
        "zero_fields": zero_fields,
        "non_zero_fields": non_zero_fields,
        "all_zero": all_zero,
    }


def classify_free(model):
    model_id = (model.get("id") or "").lower()
    suffix_free = model_id.endswith(":free")
    pricing = analyse_pricing(model.get("pricing"))

    if not suffix_free:
        return False

    if not pricing["has_pricing"]:
        return False

    if pricing["non_zero_fields"]:
        return False

    return True


def normalize(model):
    is_free = classify_free(model)

    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "context_length": model.get("context_length"),
        "is_free": is_free,
    }


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def send_wxpusher(title, content):
    if not WX_APP_TOKEN:
        print("[wxpusher] WX_APP_TOKEN not set, skipping notification")
        return False

    uids = [u.strip() for u in WX_UIDS.split(",") if u.strip()]
    if not uids:
        print("[wxpusher] WX_UIDS not set, skipping notification")
        return False

    payload = json.dumps({
        "appToken": WX_APP_TOKEN,
        "content": content,
        "summary": title,
        "contentType": 1,
        "uids": uids,
    }).encode("utf-8")

    req = Request(
        "https://wxpusher.zjiecode.com/api/send/message",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"[wxpusher] response: {result}")
            return result.get("code") == 1000
    except Exception as e:
        print(f"[wxpusher] send failed: {e}", file=sys.stderr)
        return False


# --- Main execution ---

all_models = fetch_models()
normalized_models = [normalize(m) for m in all_models]

current_free = sorted(
    [m for m in normalized_models if m["is_free"]],
    key=lambda x: x["id"] or ""
)

current_ids = {m["id"] for m in current_free}

# Load previous state: prefer check_current.json, fall back to daily current.json
previous = load_json(CHECK_CURRENT, None)
if previous is None:
    previous = load_json(DAILY_CURRENT, [])
previous_ids = {m["id"] for m in previous}

added_ids = sorted(current_ids - previous_ids)

# Save current state for next comparison
CHECK_CURRENT.write_text(
    json.dumps(current_free, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

if added_ids:
    added_models = [m for m in current_free if m["id"] in added_ids]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"OpenRouter New Free Models Detected ({now_str})", ""]
    lines.append(f"New models: {len(added_models)}")
    lines.append("")

    for m in added_models:
        ctx = m.get("context_length", "N/A")
        lines.append(f"+ {m['id']}")
        if m.get("name"):
            lines.append(f"  {m['name']} (ctx: {ctx})")

    content = "\n".join(lines)
    title = f"OpenRouter: {len(added_models)} new free model(s) detected!"

    send_wxpusher(title, content)
    print(f"Detected {len(added_models)} new free models: {added_ids}")
else:
    print("No new free models detected.")
