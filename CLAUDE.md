# Hokku — session handoff

A Raspberry Pi 3 Model A+ desk appliance that calls Claude each morning, feeds it today's top UK headlines (scraped via RSS — no agentic web search), and inks a haiku to an Inky pHAT e-paper display.

The project page (`index.html`, originally `hokku.html`) is published via GitHub Pages, deployed by `.github/workflows/pages.yml` (the standard `actions/upload-pages-artifact` + `actions/deploy-pages` flow) on every push to `main`. Pages source must be set to "GitHub Actions" in the repo's Settings → Pages.

---

## Pi access

Credentials are kept out of this file and out of git. They live in a local
`.env` (gitignored) with the following keys:

```bash
PI_HOST=...
PI_USER=...
PI_PASSWORD=...
```

Load it before running any of the commands in this doc:

```bash
set -a; . .env; set +a
```

```bash
sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST"
```

---

## What's already done

### Files on the Pi

| Path | Purpose |
|---|---|
| `/home/pi/hokku.py` | Main script (see below) — mirrored at `hokku.py` in this repo, deploy with `scp` (see Quick test commands) |
| `/etc/hokku.env` | `ANTHROPIC_API_KEY` + `HOKKU_MODEL=claude-opus-4-8` — `640 root:pi` |
| `/etc/cron.d/hokku` | Fires daily as `pi` user, logs to `/var/log/hokku.log`. Time is self-adjusting — see [Dawn tracking](#dawn-tracking) |

`hokku.py` now also exists locally in this repo (it didn't before 2026-06-30 — pulled down with `scp` from the Pi). Treat the Pi as the source of truth at runtime, but edit locally and `scp` over rather than editing in place over SSH, so the repo copy doesn't drift.

### Hardware config (`/boot/config.txt`)
- `dtparam=i2c_arm=on` — I2C for the pHAT's EEPROM
- `dtparam=spi=on` — SPI for the display
- `dtoverlay=spi0-0cs` — frees GPIO8 (CS0) from the SPI hardware so the driver can manage it manually

### Python environment (system Python 3.7, sudo path)

| Package | Version | Notes |
|---|---|---|
| inky | latest main (2.4.0+) | installed from GitHub source — has variant 23 support |
| Pillow | 9.5.0 | last version supporting Python 3.7; needed for `Image.Dither` |
| numpy | 1.16.2 | system package, sufficient |
| spidev | 3.4 | system package; despite version, `xfer3` IS present |
| smbus2 | 0.6.1 | for I2C EEPROM reads |
| RPi.GPIO | 0.7.0 | system package |

**Why not the pip-packaged inky?** The installed 2.4.0 on PyPI doesn't map EEPROM variant 23 to `InkyJD79661`. The GitHub main branch does. Installed with:
```bash
git clone --depth 1 https://github.com/pimoroni/inky /tmp/inky_src
sudo pip3 install --no-deps /tmp/inky_src
```

**Why no gpiod 2.x?** `gpiod` 2.x isn't available for Python 3.7 on armv7l. The inky library imports `gpiod` and `gpiodevice` which require gpiod 2.x. This is solved in `hokku.py` with runtime stubs injected into `sys.modules` before inky is imported (see below).

### The display

- **Inky pHAT**, EEPROM variant **23** → driver class `InkyJD79661`
- 250 × 122, four-colour: BLACK=0, WHITE=1, YELLOW=2, RED=3
- Palette: `[[0,0,0], [255,255,255], [255,255,0], [255,0,0]]`
- Pins: RESET=GPIO27, BUSY=GPIO17, DC=GPIO22, CS=GPIO8

---

## hokku.py — architecture

The script has three layers:

**1. gpiod/gpiodevice stubs** (top of file, before any inky import)  
Injects fake `gpiod` and `gpiodevice` modules into `sys.modules` backed by `RPi.GPIO`. Key classes:
- `_Value` — `ACTIVE=1`, `INACTIVE=0`
- `_LineSettings` — stores direction, initial value, and bias (`is_input`, `initial_high`, `pull_up`) — the BUSY pin (GPIO17) is requested with `pull_up=True` so it reads HIGH when idle
- `_Lines` — wraps RPi.GPIO; `set_value`, `get_value`, `wait_edge_events`, `read_edge_events`
- `_Chip` — returns integer pin offsets, returns `_Lines` from `request_lines()`

**2. Headlines + Claude API call**  
`fetch_headlines(url, limit=8)` pulls `<item><title>` values from three RSS feeds (`FEEDS` dict — BBC News, Sky News, The Guardian) via `requests` + stdlib `xml.etree.ElementTree`, no extra dependency. The headlines are pasted directly into the prompt as plain text context. `requests.post` to `/v1/messages` then runs as a **plain completion call — no tools, no agentic search**, `max_tokens=300`. Takes the **last 3 non-empty lines** of the text response (Claude sometimes adds a brief preamble before the haiku).

> Switched away from the `web_search_20250305` tool because each agentic search run was separately billed and racked up ~£6 in one day of test runs. RSS injection is a single flat-rate completion call.

**3. Render + display**  
`render()` creates a **`"P"`-mode (palette) PIL image** — `img.putpalette()` set directly to the JD79661's `DESATURATED_PALETTE` order (`BLACK=0, WHITE=1, YELLOW=2, RED=3`), text and seal drawn with flat palette-index fills (`fill=BLACK` / `fill=RED`), not RGB tuples. This makes `set_image()` on the JD79661 driver take its `dither=Image.Dither.NONE` fast path (triggered when the image is mode `"P"` with a 4-colour palette) instead of quantising RGB → 4-colour with Floyd–Steinberg dithering.
>
> The old RGB + dithering path was the root cause of three display bugs fixed together: grainy/speckled text (anti-aliased glyph edges aren't pure palette colours, so dithering error diffused noise across the image — confirmed empirically: stray yellow pixels appeared even though yellow was never drawn), a washed-out/invisible-looking red seal (dithering diluted the small 20×20 block against the dithered grey background), and text overflowing the 250px-wide screen (no fit check existed; a realistic 7-syllable haiku line measured 404px wide at the old fixed 18pt).
>
> `render()` now auto-fits font size per render: tries `ImageFont.truetype(path, size)` descending from 18pt to a 10pt floor (verified: 10pt fits even a 41-character worst-case line with 6px margin each side), picks the largest size where the widest of the three lines fits within `width - 12`, and recomputes line spacing (`round(size * 1.45)`) accordingly. Falls back to `ImageFont.load_default()` (fixed size, no fit check) only if no system TTF is found, same as before.
>
> The red seal (bottom-right corner, `ss = 24`px square, `mg = 5`px margin from the edges) carries a yellow kanji centred inside it: `SEAL_KANJI = "朝"` ("morning"), drawn with `CJK_FONT_PATH = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"` — the only CJK-capable font installed on the Pi (confirmed via `fc-list`; Noto CJK is not present). Font size is `ss - 4`, centred using `draw.textbbox` to account for glyph bearing. Kanji is drawn in `fill=YELLOW` to stay on the same palette-index fast path as the rest of the image.

---

## Dawn tracking

Added 2026-07-10. After a successful `display.show()`, `hokku.py` computes tomorrow's sunrise (the moment the solar disk crests the horizon, i.e. standard "sunrise" — not civil twilight) and rewrites the minute/hour fields of `/etc/cron.d/hokku` in place, so the daily wake time drifts with dawn through the year instead of sitting fixed at one clock time.

- **Location** is `HOKKU_LAT`/`HOKKU_LON` (env vars, default 51.9410/-1.5453 — Chipping Norton), read the same way as `HOKKU_MODEL` from `/etc/hokku.env`.
- **No new dependencies.** The Pi has no `astral` package and no `atd` running (`at` isn't installed), so sunrise is computed with a self-contained NOAA-style sunrise-equation implementation (`sunrise_utc`, `_julian_day`, `_julian_to_datetime` in `hokku.py`) — pure `math`/`calendar`, no network call. Cross-checked against `api.sunrise-sunset.org` for Chipping Norton on two dates (Jan and Jul 2026); both agreed within ~3 minutes, which is expected accuracy for this simplified equation and plenty precise for waking an appliance.
- **Local time conversion** goes UTC epoch → `datetime.fromtimestamp()`, which resolves against the Pi's system timezone (`Europe/London`, DST-aware per `timedatectl`) — no `zoneinfo`/`pytz` needed (`zoneinfo` isn't available on this Pi's Python 3.7 anyway).
- **`schedule_next_run()`** rewrites only the cron line's leading `minute hour` fields via a regex anchored on the `* * *` day/month/weekday fields, leaving the rest of the line (user, command, log redirect) untouched. It requires an exact single match or raises — a cron file that doesn't look as expected is left alone rather than silently corrupted.
- **Failure is non-fatal.** The reschedule runs after the haiku has already been inked, wrapped in its own `try/except` — a scheduling failure logs to stderr (and hence `/var/log/hokku.log`) but doesn't fail the run. Worst case, tomorrow's run fires at whatever time was last successfully written, which self-corrects on the next successful run.
- Writing `/etc/cron.d/hokku` needs root; this works unmodified because the cron job itself already invokes the script via `sudo bash -c '...'`, so `hokku.py` is already running as root when it gets here.

---

## `set_border()` is dead code on this panel

`Inky.set_border(colour)` exists on the `InkyJD79661` driver (`inky_jd79661.py`) and looks like it should tint a border around the display, but it's a no-op on this hardware. It only sets `self.border_colour`; the actual SPI command that configures the panel's border register (`JD79661_CDI`, sent during `setup()`) is hardcoded to `self._send_command(JD79661_CDI, [0x37])` and never reads `self.border_colour` back. Confirmed by grepping the whole driver file — the attribute is written in `__init__` and `set_border()` and read nowhere else.

(The border-pixel-budget logic that *does* work this way lives only in the older black/white/red drivers — `inky.py`'s base `Inky` class and `inky_ssd1608.py` — not in the colour JD79661 driver this Pi actually loads.)

We added `display.set_border(YELLOW)` to `hokku.py` on 2026-06-30, then removed it once this was confirmed — it had no visible effect and wasn't the source of an "overlap" seen on the physical display, which is unexplained but unrelated to this call.

---

## Quick test commands

All of these assume credentials are loaded first: `set -a; . .env; set +a`
(see [Pi access](#pi-access)).

```bash
# Deploy local edits to the Pi (repo copy is not auto-synced)
sshpass -p "$PI_PASSWORD" scp -o StrictHostKeyChecking=no hokku.py "$PI_USER@$PI_HOST:/home/pi/hokku.py"

# API only (no display) — verify haiku generation
sshpass -p "$PI_PASSWORD" ssh "$PI_USER@$PI_HOST" \
  'sudo bash -c "set -a; . /etc/hokku.env; set +a; python3 /tmp/hokku_test2.py"'

# Full run
sshpass -p "$PI_PASSWORD" ssh "$PI_USER@$PI_HOST" \
  'sudo bash -c "set -a; . /etc/hokku.env; set +a; python3 /home/pi/hokku.py"'

# Check cron log
sshpass -p "$PI_PASSWORD" ssh "$PI_USER@$PI_HOST" 'sudo tail -20 /var/log/hokku.log'

# Live BUSY pin reading
sshpass -p "$PI_PASSWORD" ssh "$PI_USER@$PI_HOST" \
  'python3 -c "import RPi.GPIO as G; G.setmode(G.BCM); G.setup(17,G.IN,pull_up_down=G.PUD_UP); import time; [print(G.input(17)) or time.sleep(0.2) for _ in range(10)]"'
```
