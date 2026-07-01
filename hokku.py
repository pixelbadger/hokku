#!/usr/bin/env python3
"""
Hokku — reads UK morning papers via Claude, inks a haiku to the Inky pHAT.

gpiod 2.x is not available for Python 3.7 on ARM, so we inject RPi.GPIO-backed
stubs for both `gpiod` and `gpiodevice` before inky is imported.
"""

# ── gpiod / gpiodevice stubs ─────────────────────────────────────────────────
import sys
import types, time
from datetime import datetime, timedelta
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

_BUSY_PIN = 17   # must match inky_jd79661.BUSY_PIN

class _Value:
    ACTIVE   = 1
    INACTIVE = 0

class _Direction:
    INPUT  = "input"
    OUTPUT = "output"

class _Edge:
    FALLING = "falling"
    RISING  = "rising"

class _Bias:
    DISABLED = "disabled"
    PULL_UP  = "pull_up"

class _LineSettings:
    def __init__(self, direction=None, output_value=None, bias=None,
                 edge_detection=None, debounce_period=None):
        self.is_input     = (direction == _Direction.INPUT)
        self.initial_high = (output_value == _Value.ACTIVE)
        self.pull_up      = (bias == _Bias.PULL_UP)

class _Lines:
    """Minimal gpiod LineRequest shim backed by RPi.GPIO."""
    def __init__(self, config):
        for pin, s in config.items():
            if s.is_input:
                pud = GPIO.PUD_UP if s.pull_up else GPIO.PUD_OFF
                GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
            else:
                GPIO.setup(pin, GPIO.OUT,
                           initial=GPIO.HIGH if s.initial_high else GPIO.LOW)

    def set_value(self, pin, value):
        GPIO.output(pin, GPIO.HIGH if value == _Value.ACTIVE else GPIO.LOW)

    def get_value(self, pin):
        return _Value.ACTIVE if GPIO.input(pin) else _Value.INACTIVE

    def wait_edge_events(self, timeout):
        secs = timeout.total_seconds() if isinstance(timeout, timedelta) else float(timeout)
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            if GPIO.input(_BUSY_PIN) == GPIO.LOW:
                return True
            time.sleep(0.01)
        return False

    def read_edge_events(self):
        return []

class _Chip:
    def __init__(self, path=None):
        pass

    def get_info(self):
        class _I:
            label = "pinctrl-bcm2835"
        return _I()

    def line_offset_from_id(self, id_val):
        if isinstance(id_val, int):
            return id_val
        if isinstance(id_val, str):
            s = id_val.upper()
            if s.startswith("GPIO"):
                return int(s[4:])
        return int(id_val)

    def get_line_info(self, offset):
        class _LI:
            used = False; name = ""; consumer = ""
        return _LI()

    def request_lines(self, consumer="", config=None):
        return _Lines(config or {})

# build fake gpiod
_gpiod = types.ModuleType("gpiod")
_gpiod.Chip        = _Chip
_gpiod.LineSettings = _LineSettings
_gpiod.is_gpiochip_device = lambda p: "/dev/gpiochip" in p

_line = types.ModuleType("gpiod.line")
_line.Value     = _Value
_line.Direction = _Direction
_line.Edge      = _Edge
_line.Bias      = _Bias
_gpiod.line = _line
sys.modules["gpiod"]      = _gpiod
sys.modules["gpiod.line"] = _line

# build fake gpiodevice
_gpiodev = types.ModuleType("gpiodevice")
_gpiodev.friendly_errors     = False
_gpiodev.find_chip_by_platform = lambda: _Chip()
_gpiodev.check_pins_available  = lambda chip, pins, fatal=True: True
for _sub in ("errors", "platform"):
    sys.modules[f"gpiodevice.{_sub}"] = types.ModuleType(f"gpiodevice.{_sub}")
sys.modules["gpiodevice"] = _gpiodev

# ── now safe to import inky ───────────────────────────────────────────────────
import os, json
import urllib.request
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
from inky.auto import auto

# JD79661 palette order — matches inky.inky_jd79661.DESATURATED_PALETTE
DESATURATED_PALETTE = [[0, 0, 0], [255, 255, 255], [255, 255, 0], [255, 0, 0]]
BLACK, WHITE, YELLOW, RED = 0, 1, 2, 3

FEEDS = {
    "BBC News":    "http://feeds.bbci.co.uk/news/rss.xml",
    "Sky News":    "https://feeds.skynews.com/feeds/rss/home.xml",
    "The Guardian": "https://www.theguardian.com/uk/rss",
}

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
]

CJK_FONT_PATH = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
SEAL_KANJI = "朝"  # "morning" — the seal marks the daily ritual


def fetch_headlines(url, limit=8):
    """Return up to `limit` <item><title> values from an RSS feed."""
    req = urllib.request.Request(url, headers={"User-Agent": "hokku/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    titles = [item.findtext("title", "").strip() for item in root.findall(".//item")]
    return [t for t in titles if t][:limit]


def render(haiku, display, width, height):
    """Render three haiku lines to a palette-mode image for set_image()."""
    img = Image.new("P", (width, height), color=WHITE)
    img.putpalette([c for rgb in DESATURATED_PALETTE for c in rgb])
    draw = ImageDraw.Draw(img)

    font_path = None
    for path in FONT_PATHS:
        if os.path.exists(path):
            font_path = path
            break

    lines  = [l.strip() for l in haiku.split("\n") if l.strip()]
    margin = 6

    if font_path:
        font, line_h, size = None, None, None
        for size in range(18, 9, -1):
            candidate = ImageFont.truetype(font_path, size)
            widths = [draw.textbbox((0, 0), l, font=candidate)[2] for l in lines]
            if max(widths) <= width - 2 * margin:
                font = candidate
                break
        if font is None:
            font = ImageFont.truetype(font_path, 10)
            size = 10
        line_h = round(size * 1.45)
    else:
        font = ImageFont.load_default()
        line_h = 26

    y = max(4, (height - line_h * len(lines)) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font); w = bbox[2] - bbox[0]
        draw.text(((width - w) // 2, y), line, fill=BLACK, font=font)
        y += line_h

    # red seal — bottom-right corner
    ss, mg = 24, 5
    seal_left, seal_top = width - ss - mg, height - ss - mg
    seal_right, seal_bottom = width - mg, height - mg
    draw.rectangle([seal_left, seal_top, seal_right, seal_bottom], fill=RED)

    # yellow kanji centred in the seal
    if os.path.exists(CJK_FONT_PATH):
        kanji_font = ImageFont.truetype(CJK_FONT_PATH, ss - 4)
        bbox = draw.textbbox((0, 0), SEAL_KANJI, font=kanji_font)
        kw, kh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        kx = seal_left + (ss - kw) // 2 - bbox[0]
        ky = seal_top + (ss - kh) // 2 - bbox[1]
        draw.text((kx, ky), SEAL_KANJI, fill=YELLOW, font=kanji_font)

    # date — smallest legible size, aligned with the seal, printed once at composition
    date_str = datetime.now().strftime("%-d %b")
    date_font = ImageFont.truetype(font_path, 8) if font_path else ImageFont.load_default()
    dbbox = draw.textbbox((0, 0), date_str, font=date_font)
    dw, dh = dbbox[2] - dbbox[0], dbbox[3] - dbbox[1]
    dx = seal_left - mg - dw
    dy = seal_top + (ss - dh) // 2 - dbbox[1]
    draw.text((dx, dy), date_str, fill=BLACK, font=date_font)

    return img


# ── fetch, compose, render — any failure here exits before the panel is touched ──
try:
    context = "\n\n".join(
        source + ":\n" + "\n".join("- " + t for t in fetch_headlines(url))
        for source, url in FEEDS.items()
    )

    prompt = (
        "Here are this morning's top UK headlines.\n\n"
        + context +
        "\n\nWrite ONE original haiku, three lines 5-7-5, that catches "
        "the mood of the morning — not any single headline. "
        "Output only the three lines of the haiku — no title, no preamble, no explanation."
    )

    body = json.dumps({
        "model":      os.environ.get("HOKKU_MODEL", "claude-opus-4-8"),
        "max_tokens": 300,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key":         os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read())
    haiku_raw = "\n".join(b["text"] for b in payload["content"] if b["type"] == "text").strip()

    if not haiku_raw:
        raise RuntimeError("no text in response; stop_reason={}".format(payload.get("stop_reason")))

    lines = [l.strip() for l in haiku_raw.splitlines() if l.strip()]
    haiku = "\n".join(lines[-3:])

    display = auto()
    img = render(haiku, display, display.width, display.height)
except Exception as exc:
    print(f"hokku: failed to compose this morning's haiku: {exc}", file=sys.stderr)
    sys.exit(1)

# ── ink it — the only calls that touch the panel, reached only on success ──
print(haiku)
display.set_image(img)
display.show()
