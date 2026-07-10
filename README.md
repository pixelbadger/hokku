# Hokku

A Raspberry Pi 3 Model A+ desk appliance that calls Claude each morning, feeds
it the day's top UK headlines, and inks an original haiku to an Inky pHAT
e-paper display. The project page — the idea, the build, the honest notes —
is at [pixelbadger.github.io/hokku](https://pixelbadger.github.io/hokku).

This file covers the physical build. The page describes what the thing is;
this describes how to make one.

## Build

1. Flash Raspberry Pi OS Lite to a microSD card.
2. Enable I2C and SPI (`raspi-config`), and add `dtoverlay=spi0-0cs` to
   `/boot/config.txt` — this frees GPIO8 (CS0) from the SPI hardware so the
   display driver can manage it manually.
3. Seat the Inky pHAT on the 40-pin header. No wiring required.
4. Install the Python dependencies system-wide (`sudo pip3`): `inky` from the
   [GitHub source](https://github.com/pimoroni/inky) (not the PyPI package —
   see the compatibility shim note below), Pillow 9.5.0, numpy, spidev,
   smbus2, and RPi.GPIO.
5. Copy `hokku.py` to `/home/pi/hokku.py`.
6. Create `/etc/hokku.env` (mode `640`, owner `root:pi`) with
   `ANTHROPIC_API_KEY` and `HOKKU_MODEL`. Optionally add `HOKKU_LAT`/`HOKKU_LON`
   to override the dawn-tracking location (defaults to Chipping Norton).
7. Add `/etc/cron.d/hokku` to fire once a day, sourcing the env file before
   running the script. The time only needs to be roughly right — after each
   successful run, `hokku.py` recalculates tomorrow's sunrise and rewrites the
   cron entry's minute/hour fields itself, so the wake time drifts with dawn
   through the year.
8. Test with a manual run and check the log after the first scheduled one:
   ```bash
   sudo bash -c 'set -a; . /etc/hokku.env; set +a; python3 /home/pi/hokku.py'
   sudo tail -20 /var/log/hokku.log
   ```

## The gpiod/gpiodevice compatibility shim

`gpiod` 2.x isn't available for Python 3.7 on armv7l, but the `inky` library's
GPIO backend imports `gpiod` and `gpiodevice` directly. `hokku.py` works
around this by injecting fake `gpiod`/`gpiodevice` modules into
`sys.modules`, backed by `RPi.GPIO`, before `inky` is ever imported:

```python
# gpiod 2.x isn't available for Python 3.7 on armv7l; inky imports gpiod
# and gpiodevice directly, so we inject RPi.GPIO-backed stand-ins first.
import sys, types
import RPi.GPIO as GPIO

_gpiod = types.ModuleType("gpiod")
# ... minimal Chip / LineSettings / line-request shim backed by RPi.GPIO ...
sys.modules["gpiod"] = _gpiod
sys.modules["gpiodevice"] = _gpiodev

from inky.auto import auto  # now safe to import
```

See `hokku.py` for the full shim.
