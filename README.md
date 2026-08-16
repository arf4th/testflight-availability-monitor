# TestFlight Availability Monitor

Monitors iOS TestFlight availability from a public link aggregator and sends a Telegram notification when a new app becomes available. Monitoring runs on a GitHub Actions schedule; a separate Cloudflare Worker provides an interactive Telegram bot for browsing current availability.

**Stack:** Python (monitor) · GitHub Actions (scheduling) · Cloudflare Workers / Python Workers (interactive bot)

---

## Features

- iOS TestFlight availability monitoring against a public data source
- Automatic scheduled monitoring via GitHub Actions
- Detection of newly available apps (diffed against prior state)
- Telegram notifications for newly available apps
- Interactive Telegram bot for browsing current availability
- Paginated "Currently Available" listing
- Monitor status / about interface in the bot
- Persistent state tracking via `state.json`

## Architecture

The project is split into two independently deployed components with distinct responsibilities.

```
                    ┌─────────────────────┐
                    │   TestFlight Data    │
                    │    links.json        │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    monitor.py        │
                    │   (GitHub Actions)   │
                    └──────────┬───────────┘
                               │
                       compare state.json
                               │
                       ┌───────┴───────┐
                       │               │
                    no change       new app
                       │               │
                     done          Telegram
                                     alert


              Telegram User
                    │
                    ▼
            Cloudflare Worker
                    │
             Telegram webhook
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
      /start    Available     Status/About
```

**1. GitHub Actions + Python monitor** (this repository)

Runs `monitor.py` on a schedule. It fetches the upstream data source, filters currently available iOS apps, compares the result against `state.json`, and sends a Telegram notification for any app that is newly available. Updated state is committed back to the repository.

**2. Cloudflare Worker Telegram bot** (`testflight-telegram-bot`, separate project)

Hosts the interactive side of the Telegram bot — `/start`, "Currently Available" browsing with pagination, "Monitor Status", and "About". It receives Telegram updates through a webhook and is not involved in the 5-minute monitoring loop.

The separation is intentional: Telegram does not allow `getUpdates` polling while a webhook is registered on the same bot, so the two responsibilities — scheduled monitoring/notifications, and interactive webhook-driven UI — are kept in separate processes.

> This repository previously used `getUpdates` polling for the interactive bot. That approach has been replaced by the Cloudflare Worker webhook described above. `monitor.py` only calls the Telegram Bot API to send notification messages — it does not poll or serve a webhook.

## How It Works

1. `monitor.py` fetches [`links.json`](https://raw.githubusercontent.com/pluwen/awesome-testflight-link/main/data/links.json) from the `awesome-testflight-link` project.
2. Entries are filtered to those with `status == "Y"`, a `tables` list containing `"ios"`, a non-empty `app_name`, and a valid join code.
3. The filtered set is compared against the previous contents of `state.json`.
4. Apps present in the current set but absent from the previous state are treated as newly available and trigger a Telegram notification containing the app name, TestFlight join URL, and availability status.
5. `state.json` is overwritten with the current set and, if changed, committed by the workflow.

Two safeguards are built into `monitor.py`:

- **Empty-result protection** — if the upstream source returns no usable iOS entries, the run aborts before writing state, rather than committing an empty state (which would otherwise fire a notification for every previously known app on the next run).
- **First-run baseline** — if `state.json` does not exist yet, the current set is saved as a baseline with no notifications sent, avoiding a burst of alerts for every app that was already available.

Notifications only cover apps that are newly available compared to the last recorded state. The bot's "Currently Available" listing (served by the Cloudflare Worker) reflects the same upstream source, not just newly-available apps.

## Repository Structure

```
.
├── monitor.py                     # Monitoring + notification logic
├── state.json                     # Last known set of available iOS apps (committed by CI)
├── telegram_state.json            # Legacy artifact from the earlier polling implementation
├── .github/
│   └── workflows/
│       └── monitor.yml            # Scheduled + manually-triggered monitoring workflow
└── README.md
```

`telegram_state.json` is left over from the earlier `getUpdates`-based implementation and is not read or written by the current `monitor.py`. It is not required for the current architecture.

The Cloudflare Worker (`src/entry.py`, `wrangler.jsonc`, deployment config) lives in the separate `testflight-telegram-bot` project, not in this repository.

## Requirements

- Python 3.x (standard library only — no third-party dependencies)
- A Telegram bot token and target chat ID
- A GitHub repository with Actions enabled (for scheduled runs)

## Configuration

### GitHub Actions Secrets

Set under **Repository Settings → Secrets and variables → Actions**:

| Secret | Used for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Authenticates `monitor.py` against the Telegram Bot API |
| `TELEGRAM_CHAT_ID` | Destination chat for notification messages |

Neither value should ever be committed to the repository.

### Cloudflare Worker Secrets

The Cloudflare Worker (separate project) requires the same two credentials, configured as Worker secrets rather than GitHub Actions secrets:

| Secret | Used for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Authenticates the Worker against the Telegram Bot API and validates the webhook |
| `TELEGRAM_CHAT_ID` | Used by the interactive bot's status/about responses |

## Local Development

Run the monitor directly:

```bash
export TELEGRAM_BOT_TOKEN="<your-bot-token>"
export TELEGRAM_CHAT_ID="<your-chat-id>"
python3 monitor.py
```

`monitor.py` reads both variables from the environment; there is no config file. In GitHub Actions, the same variables are supplied via repository secrets rather than the shell environment.

The Cloudflare Worker is developed and deployed independently using Cloudflare's Wrangler tooling; see [Cloudflare Worker Setup](#cloudflare-worker-setup) below.

## Telegram Bot

The interactive bot is served by the Cloudflare Worker, not by this repository's monitoring script.

| Menu item | Behavior |
|---|---|
| 🟢 Currently Available | Lists currently available iOS TestFlight apps from the upstream source, paginated (page size: 6) |
| 📊 Monitor Status | Displays monitor status information |
| ℹ️ About | Displays information about the bot |

Navigation uses Telegram inline keyboard callback queries.

## GitHub Actions

Workflow: [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml)

- **Trigger:** `schedule: */5 * * * *` (approximately every 5 minutes) and `workflow_dispatch` for manual runs
- **Permissions:** `contents: write` (required to commit `state.json`)
- **Concurrency:** a single concurrency group prevents overlapping runs
- **Steps:** `actions/checkout@v4` → `actions/setup-python@v5` (Python 3.x) → run `python monitor.py` with `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from secrets → commit and push `state.json` if it changed

## Deployment

**Monitor (this repository):** runs automatically via the GitHub Actions schedule once secrets are configured. It can also be run on demand via `workflow_dispatch`.

**Cloudflare Worker (`testflight-telegram-bot`, separate project):** deployed with Wrangler via `npm run deploy`, which invokes the Python Workers deployment command. Cloning this repository does not deploy the Worker — it is a separate project with its own deployment.

## Testing

**Monitor, locally:**

```bash
python3 monitor.py
```

Expected output: the source is fetched, the number of currently available iOS apps is printed, the result is compared against `state.json`, any newly available apps are reported, and `state.json` is updated on disk.

**GitHub Actions:**

Trigger the workflow manually via `workflow_dispatch` from the Actions tab and inspect the run logs and the resulting diff (or absence of one) to `state.json`.

**Telegram bot:**

Send `/start` to the bot and exercise the menu — Currently Available (including pagination), Monitor Status, and About.

There is no built-in mechanism to simulate a real new TestFlight app becoming available; testing notification delivery relies on an actual change in the upstream source or a temporary local edit to `state.json` to force a diff.

## Troubleshooting

**Telegram HTTP 409 — `Conflict: can't use getUpdates method while webhook is active`**

This occurs when something attempts `getUpdates` polling while a webhook is registered for the bot. In the current architecture, the Cloudflare Worker owns the webhook and `monitor.py` never calls `getUpdates` — it only sends messages. If this error appears, check for a stray process or older deployment still polling with the same bot token.

**Cloudflare Worker webhook returning HTTP 500**

Inspect the Worker's logs using Wrangler's tailing/logging output from the `testflight-telegram-bot` project to identify the failure.

## Security

- Never commit `TELEGRAM_BOT_TOKEN` to source control.
- Treat `TELEGRAM_CHAT_ID` as private configuration if your chat is not intended to be public.
- Use GitHub Actions secrets for the monitor; use Cloudflare Worker secrets for the bot. Do not hardcode either credential in source.
- If a bot token is ever exposed publicly, revoke and regenerate it via [@BotFather](https://t.me/BotFather) immediately.

## Limitations

- Monitoring is entirely dependent on the upstream `awesome-testflight-link` data source; if it is stale, unavailable, or changes format, detection is affected.
- Detection runs on the GitHub Actions schedule (approximately every 5 minutes). GitHub Actions cron schedules are best-effort, not a hard real-time guarantee, so actual run timing can drift, especially under GitHub-wide load.
- The bot's "Currently Available" listing reflects the same upstream source and is not independently verified against Apple/TestFlight.
- Real TestFlight slot availability can change at any time, independent of when the monitor last ran.

## Future Improvements

The following are potential directions, not implemented features:

- Database-backed state instead of a committed JSON file
- Richer filtering (categories, search)
- Per-user notification preferences
- Monitoring analytics/history
- Additional deployment automation for the Cloudflare Worker

## License

License information has not yet been specified.
