import json
import os
import urllib.request
import urllib.parse
from pathlib import Path


SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "pluwen/awesome-testflight-link/main/data/links.json"
)

STATE_FILE = Path("state.json")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def fetch_source():
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "TestFlightAvailabilityMonitor/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_ios_available(data):
    if not isinstance(data, dict):
        raise ValueError("Source JSON is not an object.")

    links = data.get("_links")

    if not isinstance(links, dict):
        raise ValueError("Source JSON does not contain '_links'.")

    available = {}

    for join_code, app in links.items():
        if not isinstance(app, dict):
            continue

        status = str(app.get("status", "")).strip().upper()
        tables = app.get("tables", [])

        if not isinstance(tables, list):
            continue

        if status != "Y":
            continue

        if "ios" not in [str(x).lower() for x in tables]:
            continue

        app_name = str(app.get("app_name", "")).strip()

        if not app_name:
            continue

        join_code = str(join_code).strip()

        if not join_code:
            continue

        testflight_url = f"https://testflight.apple.com/join/{join_code}"

        # Use the TestFlight join code as the stable identity.
        available[join_code] = {
            "name": app_name,
            "url": testflight_url,
        }

    if not available:
        raise ValueError(
            "No iOS Available apps were found. "
            "Refusing to update state because the source may have changed."
        )

    return available


def load_previous_state():
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("State is not an object.")

        return state

    except Exception as exc:
        raise RuntimeError(f"Could not read state.json: {exc}") from exc


def save_state(current):
    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(current, file, indent=2, ensure_ascii=False)
        file.write("\n")

    temp_file.replace(STATE_FILE)


def send_telegram_message(text):
    api_url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": "false",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TestFlightAvailabilityMonitor/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def send_notifications(new_apps):
    if not new_apps:
        return

    header = "🟢 New TestFlight app(s) became Available!\n\n"

    messages = []
    current_message = header

    for app in new_apps:
        item = (
            "📱 "
            f"{app['name']}\n"
            "🔗 "
            f"{app['url']}\n"
            "✅ Newly became Available\n\n"
        )

        # Keep safely below Telegram's message size limit.
        if len(current_message) + len(item) > 3500:
            messages.append(current_message.rstrip())
            current_message = header + item
        else:
            current_message += item

    if current_message.strip() != header.strip():
        messages.append(current_message.rstrip())

    for message in messages:
        send_telegram_message(message)


def main():
    print("Fetching TestFlight source...")
    data = fetch_source()

    current = parse_ios_available(data)

    print(f"Found {len(current)} currently Available iOS apps.")

    previous = load_previous_state()

    # First successful run = baseline.
    # Do NOT send 250+ notifications on initial setup.
    if previous is None:
        print("No previous state found.")
        print("Creating initial baseline without notifications.")
        save_state(current)
        return

    previous_keys = set(previous.keys())
    current_keys = set(current.keys())

    newly_available_keys = sorted(current_keys - previous_keys)

    new_apps = [current[key] for key in newly_available_keys]

    if new_apps:
        print(f"Found {len(new_apps)} newly Available app(s).")

        # Only update persistent state after Telegram succeeds.
        send_notifications(new_apps)
    else:
        print("No newly Available apps.")

    # State represents the CURRENT Available set.
    #
    # Therefore:
    # Available -> Full/No/Removed -> Available
    # becomes a new event and triggers another notification.
    save_state(current)

    print("State updated successfully.")


if __name__ == "__main__":
    main()