<img src="src/pi_display_microservice/assets/athena.svg" alt="Athena's shield: a bronze hoplite shield bearing an owl on an olive branch" width="112" align="right">

# pi-display-microservice

Athena's status board: what [Athena](https://github.com/georgeslane/athena) is doing, on a [Pimoroni Display HAT Mini](https://pinout.xyz/pinout/display_hat_mini) on a Raspberry Pi. In between, it can show a slideshow of your photos.

![The board while idle, thinking, using a tool, waiting for approval, after a failed request, and offline](docs/board.png)

- **Idle:** the time, and how your last request went.
- **Working:** your request, what Athena is doing now (thinking, or which tool it's using), the tools it has used and how long it's taken. A light runs round the shield, and the LED is blue.
- **Needs you:** a tool is waiting for your approval in Telegram. The shield pulses amber, the LED flashes amber, and the clock counts down to when the request is denied automatically.
- **Offline:** Athena isn't answering. If that's for a reason other than Athena not running, such as a wrong token, the board says what it is.

![The photo page: a photo just liked, a photo just disliked, and the page while it gets the album](docs/photos.png)

The photo page shows the photos in a Google Photos album, a new one every 5 minutes. The ones you like come up more often, and the ones you dislike less often. See [The photo slideshow](#the-photo-slideshow).

## The buttons

The board has two pages: the photos, if you've given it an album, and Athena's status. It starts on the photos.

| Button | What it does |
|---|---|
| A, top left | Turns the screen off, and on again |
| B, bottom left | Moves to the next page: from the photos to Athena's status, and back |
| X, top right | On the photos: likes the photo on screen, so it comes up more often |
| Y, bottom right | On the photos: dislikes the photo on screen, so it comes up less often |

While the screen is off, any button turns it back on. It also comes on by itself whenever Athena is working or needs you, showing Athena's status, and goes off again when Athena is done. A tool waiting for your approval takes over from the photos too, until it's answered, so you never miss one. The LED shows what Athena is doing, whichever page is on screen.

## How it works

Athena and the board are separate programs. Athena publishes what it's doing through a small HTTP API, and the board asks for it. Either can be restarted, updated or replaced without the other, and the board can tell when Athena isn't running, because nothing answers.

```
 Athena (pi-assistant)                       pi-display-microservice
┌────────────────────────┐                 ┌───────────────────────────────────────────────┐
│ agent ─► StatusTracker │  GET /v1/status │ client.py   asks Athena, on a thread of its   │
│              │         │ ◄────────────── │      │      own, and keeps the latest status  │
│              ▼         │                 │      ▼                                        │
│ status API on :8091 ───┼───── JSON ────► │ app.py      the main loop, several times a    │
└────────────────────────┘                 │      │      second: draws the page on screen  │
                                           │      ├─► board.py      Athena's status page   │
 Google Photos                             │      ├─► slideshow.py  the photo page         │
┌────────────────────────┐                 │      │       ▲                                │
│ your shared album ─────┼──── photos ───► │      │   album.py      copies the album, on a │
└────────────────────────┘                 │      │                 thread of its own      │
                                           │      └─► screen.py     the LCD, LED and       │
                                           │                        buttons, or a PNG file │
                                           └───────────────────────────────────────────────┘
```

**Polling, without the waiting.** Each request tells Athena which version of its status the board already has, and Athena holds its answer until something changes, or 25 seconds pass. So a change reaches the screen at once, while the board asks only about twice a minute when nothing happens. If Athena doesn't answer, the board shows it as offline and tries again every 5 seconds.

**Drawing never waits for the network.** The client keeps the latest status on its own thread, and the album keeps a copy of its photos on disk, on another. The main loop draws the page on screen six times a second while something on it moves (the spinner, the countdown) and once a second otherwise. It only sends a frame to the screen if it has changed.

| File | What it does |
|---|---|
| `src/pi_display_microservice/status.py` | What a status is (`Snapshot`, `Status`), and reading Athena's JSON |
| `src/pi_display_microservice/client.py` | Asking Athena for its status, and noticing when it's offline |
| `src/pi_display_microservice/app.py` | The main loop: which page, what to draw, how often, the LED and the buttons; the demo |
| `src/pi_display_microservice/board.py` | Drawing Athena's status. Pure Pillow, so it runs on any computer |
| `src/pi_display_microservice/album.py` | Reading a shared Google Photos album, and keeping a copy of its photos |
| `src/pi_display_microservice/slideshow.py` | The photo page: which photo comes next, and drawing it |
| `src/pi_display_microservice/scores.py` | Likes and dislikes, and how they weight which photo comes next |
| `src/pi_display_microservice/screen.py` | The Display HAT Mini (screen, LED, buttons), and the PNG preview |
| `src/pi_display_microservice/config.py`, `cli.py` | Settings, and the `pi-display-microservice` command |
| `scripts/install.sh`, `update.sh` | Setting it up on the Pi, and updating it |

## Install on the Pi

Set up Athena first. Its status API is on by default, listening only on the Pi (`[display]` in Athena's `config.toml`). Then, on the Pi:

```bash
git clone https://github.com/georgeslane/pi-display-microservice.git ~/pi-display-microservice
cd ~/pi-display-microservice
bash scripts/install.sh
```

The script:
- installs uv and the screen's drivers
- turns on SPI
- adds you to the `spi` and `gpio` groups
- turns off `git push` for this clone
- creates `config.toml`
- starts the `pi-display-microservice` service

If it changed the boot config, it asks you to reboot first.

That boot config change is for the Pi 5. The HAT uses GPIO 9 to tell the screen pixels from commands, and SPI claims the same pin as MISO. Since kernel 6.18, a Pi 5 won't share it, so the installer adds `dtoverlay=spi0-2cs,no_miso` to `/boot/firmware/config.txt`, which tells SPI to leave it alone. The screen never sends data back, so nothing is lost.

Then check the board can hear Athena:

```bash
uv run pi-display-microservice check
```

To show your photos too, see [The photo slideshow](#the-photo-slideshow).

### Moving from the board built into pi-assistant

The board used to be part of pi-assistant, as the `pi-assistant-display` service. This installer stops and removes that service, and so does pi-assistant's `scripts/update.sh`. Then, in Athena's `config.toml`, remove `led` from `[display]` and set it here instead. Athena's `doctor` reminds you.

## Settings

`config.toml` is optional. Without one, the board uses the defaults, which work with Athena's own.

| Setting | Default | What it does |
|---|---|---|
| `athena_url` | `"http://127.0.0.1:8091"` | Athena's status API: the `host` and `port` in Athena's `[display]` |
| `token` | `""` | Only if Athena's `[display]` has a token: the same one |
| `led` | `true` | The LED: blue while Athena works, flashing amber when it needs you |
| `name`, `timezone` | `""` | The name on the board and the clock's timezone. Empty means whatever Athena says |
| `wait_seconds` | `25` | How long each request lets Athena wait for a change (at most 30) |
| `retry_seconds` | `5` | How soon to try again when Athena doesn't answer |
| `album_url` | `""` | A Google Photos album's share link, for the photo page. Empty means no photo page |

After changing it: `sudo systemctl restart pi-display-microservice`.

## The photo slideshow

**Setting it up.** In Google Photos, open the album, choose **Share**, then **Create link**, and copy the link. It starts `https://photos.app.goo.gl/`. Put it in `config.toml`:

```toml
album_url = "https://photos.app.goo.gl/…"
```

Then restart the board (`sudo systemctl restart pi-display-microservice`), and check it can read the album:

```bash
uv run pi-display-microservice check
```

Anyone with the link can see the album, so keep it in `config.toml`, which git ignores. The secret check stops a share link reaching GitHub from anywhere else.

**Which photo comes next.** Every 5 minutes the board picks a new photo at random, but not evenly. Each photo has a score, x: the number of times you've liked it, minus the number of times you've disliked it. It comes up in proportion to its weight:

| Score | Weight | |
|---|---|---|
| x ≥ 0 | 2 − 1/(1 + x) | 1 to start with, then 1.5 after a like, 1.75 after three, rising towards 2 |
| x < 0 | 1/(1 + \|x\|) | 0.5 after a dislike, 0.25 after three, falling towards 0 |

So a photo you like comes up at most twice as often as one you haven't rated, and one you dislike comes up less and less, but never disappears altogether. Every press counts, so press X three times for a photo you love. Liking or disliking doesn't change the photo on screen, and the next one is always a different photo.

**What's kept where.** The photos are downloaded at the screen's size into `~/.local/share/pi-display-microservice/photos/`, so the slideshow carries on without the internet. Every hour, the board downloads photos added to the album, and deletes those taken out of it. Your likes and dislikes are in `~/.local/share/pi-display-microservice/scores.json`, and survive restarts and updates.

**How the board reads the album.** Since March 2025, Google's Photos API can only read photos an app uploaded itself, so it can't read your albums. Instead, the board reads the album's share page, as a browser does. That page isn't an official API, so Google may change it. If it does, the photo page says it can't read the album and keeps showing the photos it already has, and `album.py` needs updating. Two limits:
- The page for a very large album only lists its first batch of photos, so the slideshow only has those. `check` says when that's the case.
- Videos show as a still.

## Athena's status API

This is the whole contract between the two projects. Athena serves it (`src/pi_assistant/status_api.py` in Athena), and this board reads it (`status.py` and `client.py` here).

**Request:** `GET /v1/status`, with `Authorization: Bearer <token>` if Athena has a token.

- `?wait=25&after=<version>`: if Athena's status is still at that version, it waits up to `wait` seconds (at most 30) for a change before answering.
- With no `after`, or a different one, it answers at once.

**Answer:**

```json
{
  "api": 1,
  "version": "3f9a1c2e-17",
  "now": 1759651200.5,
  "assistant": {"name": "Athena", "timezone": "Europe/London"},
  "status": {
    "state": "approval",
    "task": "Check the weather in London and, if it's going to rain, remind me to take an umbrella",
    "step": "Using fetch",
    "tools": ["fetch"],
    "tool": "trading212_place_order",
    "channel": "Telegram",
    "started": 1759651140.0,
    "deadline": 1759651440.0,
    "last_task": "",
    "last_error": "",
    "last_finished": 0.0,
    "updated": 1759651195.2
  }
}
```

| Field | Meaning |
|---|---|
| `api` | The API's version. It only changes if the format changes in a way old boards can't follow. A board that sees a newer one says "update pi-display-microservice". |
| `version` | Changes whenever anything in `status` does. It's opaque: compare it, don't parse it. It's different after Athena restarts. |
| `now` | Athena's clock. The board uses it to move Athena's times onto its own clock, in case they're on different machines. |
| `assistant` | The name and timezone in Athena's `[agent]` settings. |
| `status.state` | `idle`, `working` or `approval`. Never `offline`: the board decides that when nothing answers. |
| `status.task` | The start of the request being worked on. Empty if Athena's `show_task = false`. |
| `status.step` | What's happening now, such as "Thinking" or "Using fetch". |
| `status.tools` | The tools used for this task so far, oldest first. |
| `status.tool`, `channel`, `deadline` | While waiting for approval: which tool, where to approve it, and when it will be denied (0 if never). |
| `status.started` | When the task started. |
| `status.last_task`, `last_error`, `last_finished` | When idle: the previous task, why it failed (empty if it didn't) and when it ended. |
| `status.updated` | When the status last changed. |

Times are Unix timestamps in seconds.

**Rules that keep the two projects independent:**
- Athena may add fields anywhere, without changing `api`.
- The board ignores fields it doesn't know, and uses defaults for any that are missing.
- So either side can be updated first.

**Errors:**
- 401: wrong or missing token
- 404: wrong path
- 405: not GET
- 400: a `wait` that isn't a number

To see it for yourself on the Pi:

```bash
curl -s http://127.0.0.1:8091/v1/status | python3 -m json.tool
```

## Developing on your computer

Everything except the screen itself runs on any computer, so you can work on the board without the Pi:

```bash
uv sync
uv run pi-display-microservice demo --preview board.png   # cycles through example states
```

`--preview FILE` draws into a PNG instead of on the screen, and updates it as the board changes. Open it in an image viewer that reloads files, such as Preview on a Mac.

To draw what the real Athena is doing, forward its API from the Pi:

```bash
ssh -N -L 8091:127.0.0.1:8091 <your-pi>   # leave this running
uv run pi-display-microservice run --preview board.png
```

With an `album_url`, `run` starts on the photos. A preview has no buttons to change page, so add `--page status` to start on Athena's status instead.

Run the tests with `uv run python -m pytest`. They use stand-ins for the hardware, for Athena and for Google Photos (`tests/conftest.py`), so they run anywhere. With the git hooks installed (see [Keeping secrets out of GitHub](#keeping-secrets-out-of-github)), they run before every commit too. After changing the board's design or `assets/athena.svg`, redraw the images with `uv run --with resvg-py scripts/render_images.py`.

## Adding features

Some ideas: more pages (the calendar, the weather, your portfolio), buttons that do things, or richer animations. Here's where each kind of change goes.

**Changing what's drawn.**
- The photo page is drawn in `slideshow.py`. Athena's status is drawn in `board.py`:
  - `_headline` and `_sub` hold the large and small text beside the shield.
  - `_details` holds the bottom half.
  - `_draw_shield` draws the shield and its animations.
- Most of the picture is drawn once and kept until the status (or the minute) changes. Only the moving parts are drawn every frame, in `render`, which keeps the Pi's work small. Keep anything that changes every frame out of `_draw_static`.
- Add a case to `EXAMPLES` in `tests/test_board.py`, and check the result with `--preview`.

**Using the buttons.**
- `screen.wait()` returns the button pressed: `"A"` or `"B"` (left side, top and bottom), `"X"` or `"Y"` (right side), or `None`.
- `run_board` in `app.py` decides what each one does: `A` the screen, `B` the page, and `X` and `Y` like and dislike on the photos. On Athena's status, `X` and `Y` are free.
- The tests' `FakeScreen` (`tests/test_app.py`) presses buttons for you.

**More pages.**
- The pages are in `run_board`'s `pages`, in the order `B` goes through them. To add one, add it there, and draw it where `run_board` draws the photos. The slideshow shows the shape a page takes: a `render(now)` that returns the 320x240 picture.
- Pages that don't depend on Athena (a clock, the weather) can still show while Athena is offline.

**New information from Athena**, such as your next calendar event or your portfolio's value. This takes a change on both sides:
1. **In Athena:** add it to the answer in `payload()` in `src/pi_assistant/status_api.py`, as a new top-level key like `"next_event"`. Leave `api` at 1, since adding is compatible. When the new data changes, Athena should change the `version` as it does for the status (`_publish`), so a waiting board hears straight away. Add a test in Athena's `tests/test_status_api.py`.
2. **Here:** read it in `status.parse()` into a new field on `Status`, with a default for when it's missing, so the board still works with an older Athena. Then draw it, and hide it when it's missing.

**Information from elsewhere**, such as the weather.
- The board can fetch things itself, but do it like `client.py` and `album.py`: on a thread of its own, never in the drawing loop, keeping the latest answer for the loop to read.
- Remember that anything the board fetches leaves your network. That's fine for the weather; keep anything personal in Athena.

**The LED.** `Board.led()` returns which of red, green and blue are on, for a given status and moment. Flashing is just a different answer at different moments.

## Updating

```bash
cd ~/pi-display-microservice && bash scripts/update.sh
```

It pulls the latest code, updates the packages and restarts the service.

## Troubleshooting

- **Start with `journalctl -u pi-display-microservice -f`.** The board's errors say what to fix, such as:
  - SPI being off
  - missing permissions
  - GPIO 9 being taken
  - another board already using the pins, such as the old `pi-assistant-display`
- **The board says "Offline" while Athena runs:**
  - Run `uv run pi-display-microservice check`.
  - On Athena's side, check `[display] enabled` and the port, and see what `uv run pi-assistant doctor` says under "Status API".
  - If Athena was started with `pi-assistant chat` while its service was also running, the service has the port, so that's the one the board shows.
- **The board says "CAN'T SHOW ATHENA":** Athena answered, but the board couldn't use the answer. The board shows the reason, for example:
  - a wrong token
  - an `athena_url` that isn't Athena
  - an Athena with a newer API
- **The screen stays blank after installing:** reboot if the installer asked you to, and check `journalctl -u pi-display-microservice`.
- **There's no photo page:** set `album_url` in `config.toml`, and restart the board.
- **The photo page shows a message in red instead of photos:** the board couldn't read the album or download its photos, and the message says why, for example:
  - an `album_url` that isn't a share link, or an album that's no longer shared
  - no internet: the photos it has already downloaded keep showing, and it tries again every 5 minutes

  `uv run pi-display-microservice check` says what Google Photos answered.

## Keeping secrets out of GitHub

This repo has the same protections as Athena. Only `config.toml` can hold anything private (Athena's token and your album's share link), and it's git-ignored. Your photos and likes are kept outside the repo, in `~/.local/share/pi-display-microservice/`.

- **On the computer you develop on:** run `bash scripts/install-git-hooks.sh` once, with gitleaks installed (`brew install gitleaks`). Then every commit and push is checked for secrets, album share links among them, and for anything in your git-ignored `.personal-blocklist`. Each commit must pass the tests, too (`scripts/pre-commit.sh`). To skip the checks once, use `--no-verify`.
- **On GitHub:** CI runs the same scan and the tests on every push, and on every pull request into `main`, as it would be once merged. Work on `dev`, and bring changes into `main` with a pull request. Protect `main` so it only accepts pull requests that pass both checks, "Secret scan" and "Tests".
- **On the Pi:** the installer turns off `git push` for its clone, so nothing can be pushed from there.

## License

MIT (see `LICENSE`). The fonts, Cinzel and Inter, are under the SIL Open Font License (`src/pi_display_microservice/assets/fonts/*-OFL.txt`).
