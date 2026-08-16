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
TELEGRAM_STATE_FILE = Path("telegram_state.json")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PAGE_SIZE = 6


# ============================================================
# TESTFLIGHT SOURCE
# ============================================================

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


# ============================================================
# PERSISTENT TESTFLIGHT STATE
# ============================================================

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


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload=None):
    api_url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )

    if payload is None:
        payload = {}

    encoded = urllib.parse.urlencode(
        {
            key: json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else value
            for key, value in payload.items()
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        api_url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TestFlightAvailabilityMonitor/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Telegram API HTTP {exc.code} in {method}: {body}"
        ) from exc

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error in {method}: {result}"
        )

    return result.get("result")


def send_telegram_message(text, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "false",
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    telegram_api("sendMessage", payload)


def answer_callback_query(callback_query_id):
    try:
        telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
            },
        )
    except Exception as exc:
        # Callback queries expire quickly. An expired callback
        # should not crash the entire monitoring job.
        print(
            f"Warning: Could not answer Telegram callback: {exc}"
        )

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        telegram_api("editMessageText", payload)
    except RuntimeError as exc:
        error_text = str(exc)

        if "message is not modified" in error_text:
            print("Telegram message is already up to date.")
            return

        raise


# ============================================================
# TELEGRAM STATE
# ============================================================

def load_telegram_offset():
    if not TELEGRAM_STATE_FILE.exists():
        return 0

    try:
        with TELEGRAM_STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

        if not isinstance(state, dict):
            return 0

        return int(state.get("offset", 0))

    except Exception:
        print("Warning: Could not read telegram_state.json.")
        return 0


def save_telegram_offset(offset):
    temp_file = TELEGRAM_STATE_FILE.with_suffix(".tmp")

    with temp_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            {"offset": offset},
            file,
            indent=2,
        )
        file.write("\n")

    temp_file.replace(TELEGRAM_STATE_FILE)


# ============================================================
# TELEGRAM UI
# ============================================================

def main_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🟢 Currently Available",
                    "callback_data": "available:0",
                }
            ],
            [
                {
                    "text": "📊 Monitor Status",
                    "callback_data": "status",
                },
                {
                    "text": "ℹ️ About",
                    "callback_data": "about",
                },
            ],
        ]
    }


def available_keyboard(page, total_pages):
    buttons = []

    navigation = []

    if page > 0:
        navigation.append(
            {
                "text": "◀ Previous",
                "callback_data": f"available:{page - 1}",
            }
        )

    if page < total_pages - 1:
        navigation.append(
            {
                "text": "Next ▶",
                "callback_data": f"available:{page + 1}",
            }
        )

    if navigation:
        buttons.append(navigation)

    buttons.append(
        [
            {
                "text": "🏠 Main Menu",
                "callback_data": "menu",
            }
        ]
    )

    return {
        "inline_keyboard": buttons
    }


def build_available_page(available, page):
    apps = sorted(
        available.items(),
        key=lambda item: item[1]["name"].lower(),
    )

    total = len(apps)

    total_pages = max(
        1,
        (total + PAGE_SIZE - 1) // PAGE_SIZE,
    )

    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    selected = apps[start:end]

    lines = [
        "🟢 Currently Available iOS TestFlight Apps",
        "",
        f"📱 Total available: {total}",
        f"📄 Page {page + 1}/{total_pages}",
        "",
    ]

    for index, (_, app) in enumerate(
        selected,
        start=start + 1,
    ):
        lines.append(
            f"{index}. {app['name']}\n"
            f"🔗 {app['url']}\n"
        )

    return "\n".join(lines), total_pages


# ============================================================
# TELEGRAM INTERACTION
# ============================================================

def handle_start(chat_id):
    text = (
        "👋 Welcome to TestFlight Availability Monitor!\n\n"
        "🟢 I monitor iOS TestFlight apps and notify you "
        "when a new app becomes Available.\n\n"
        "What would you like to do?"
    )

    send_telegram_message(
        text,
        main_keyboard(),
    )


def handle_callback(callback_query, current):
    callback_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message")

    if not message:
        if callback_id:
            answer_callback_query(callback_id)
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    if chat_id != int(TELEGRAM_CHAT_ID):
        if callback_id:
            answer_callback_query(callback_id)
        return

    if callback_id:
        answer_callback_query(callback_id)

    if data == "menu":
        edit_telegram_message(
            chat_id,
            message_id,
            (
                "👋 TestFlight Availability Monitor\n\n"
                "What would you like to do?"
            ),
            main_keyboard(),
        )
        return

    if data == "about":
        edit_telegram_message(
            chat_id,
            message_id,
            (
                "ℹ️ About TestFlight Monitor\n\n"
                "I monitor the public TestFlight list and "
                "detect iOS apps that become Available.\n\n"
                "⏱️ Checks approximately every 5 minutes.\n"
                "🔔 New apps trigger a Telegram notification.\n"
                "🛡️ Duplicate notifications are prevented."
            ),
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🏠 Main Menu",
                            "callback_data": "menu",
                        }
                    ]
                ]
            },
        )
        return

    if data == "status":
        edit_telegram_message(
            chat_id,
            message_id,
            (
                "📊 Monitor Status\n\n"
                f"🟢 Currently Available: {len(current)} apps\n"
                "⏱️ Check interval: approximately 5 minutes\n"
                "🔔 Notifications: Active"
            ),
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🟢 View Available Apps",
                            "callback_data": "available:0",
                        }
                    ],
                    [
                        {
                            "text": "🏠 Main Menu",
                            "callback_data": "menu",
                        }
                    ],
                ]
            },
        )
        return

    if data.startswith("available:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0

        text, total_pages = build_available_page(
            current,
            page,
        )

        edit_telegram_message(
            chat_id,
            message_id,
            text,
            available_keyboard(
                page,
                total_pages,
            ),
        )


def process_telegram_updates(current):
    offset = load_telegram_offset()

    result = telegram_api(
        "getUpdates",
        {
            "offset": offset,
            "limit": 100,
            "timeout": 0,
            "allowed_updates": [
                "message",
                "callback_query",
            ],
        },
    )

    if not result:
        print("No Telegram updates.")
        return

    print(f"Processing {len(result)} Telegram update(s).")

    highest_update_id = offset - 1

    for update in result:
        update_id = update.get("update_id")

        if isinstance(update_id, int):
            highest_update_id = max(
                highest_update_id,
                update_id,
            )

        message = update.get("message")

        if message:
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            text = str(message.get("text", "")).strip()

            if (
                chat_id == int(TELEGRAM_CHAT_ID)
                and text.startswith("/start")
            ):
                handle_start(chat_id)

        callback_query = update.get("callback_query")

        if callback_query:
            handle_callback(
                callback_query,
                current,
            )

    new_offset = highest_update_id + 1

    if new_offset > offset:
        save_telegram_offset(new_offset)


# ============================================================
# NOTIFICATIONS
# ============================================================

def send_notifications(new_apps):
    if not new_apps:
        return

    header = (
        "🟢 New TestFlight app(s) became Available!\n\n"
    )

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

        if len(current_message) + len(item) > 3500:
            messages.append(
                current_message.rstrip()
            )
            current_message = header + item
        else:
            current_message += item

    if current_message.strip() != header.strip():
        messages.append(
            current_message.rstrip()
        )

    for message in messages:
        send_telegram_message(message)


# ============================================================
# MAIN
# ============================================================

def main():
    print("Fetching TestFlight source...")

    data = fetch_source()

    current = parse_ios_available(data)

    print(
        f"Found {len(current)} currently "
        "Available iOS apps."
    )

    # Process Telegram interactions first.
    process_telegram_updates(current)

    previous = load_previous_state()

    # First successful run = baseline.
    if previous is None:
        print("No previous state found.")
        print(
            "Creating initial baseline "
            "without notifications."
        )
        save_state(current)
        return

    previous_keys = set(previous.keys())
    current_keys = set(current.keys())

    newly_available_keys = sorted(
        current_keys - previous_keys
    )

    new_apps = [
        current[key]
        for key in newly_available_keys
    ]

    if new_apps:
        print(
            f"Found {len(new_apps)} "
            "newly Available app(s)."
        )

        send_notifications(new_apps)

    else:
        print("No newly Available apps.")

    save_state(current)

    print("State updated successfully.")


if __name__ == "__main__":
    main()