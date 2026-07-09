"""
led_controller.py
-----------------
Controls two 4-pin RGB LEDs via GPIO, now using SOFTWARE PWM instead of
plain HIGH/LOW output.

WHY THIS CHANGED FROM THE ORIGINAL VERSION:
The previous version drove each channel with GPIO.output(pin, HIGH/LOW)
only, so `brightness` was accepted but silently ignored (see the old
flash() docstring), and every color collapsed into one of 8 pure on/off
combinations. This version PWMs each of the 6 channel pins, so:
  - intensity_pct from the app actually changes LED brightness
  - each channel gets a continuous 0-255 -> 0-100% duty cycle, so many
    more colors are reachable, not just the 8 primaries
  - a per-LED calibration multiplier lets you make LED1 and LED2 match
    perceptually for "the same" nominal hex color (see LED*_CALIBRATION
    below -- these are placeholders, tune them by eye against a spectro
    or just visually side-by-side)

CONFIRMED HARDWARE WIRING (unchanged from before):
  LED1 (left)  — common CATHODE → common pin to GND   → HIGH = more ON
  LED2 (right) — common ANODE   → common pin to 3.3V  → LOW  = more ON

IMPORTANT CAVEAT -- PLEASE TEST THIS ON THE PI BEFORE TRUSTING IT:
RPi.GPIO's software PWM runs its timing in a background thread on the
CPU, not via a hardware timer/DMA like `pigpio` would. Flash *duration*
timing (t_on/t_off, which segmenter.py uses to sync video frames) is
untouched -- it still uses time.sleep() exactly as before -- but the
*color/brightness* signal itself could show visible flicker or add CPU
jitter while it's running concurrently with active H264 encoding during
a session. Watch for this specifically during a real recording. If it's
a problem, the fix is to switch this file to `pigpio` (hardware-timed
PWM, off the main CPU) or move to an external PWM driver like a PCA9685
I2C board -- both are drop-in replacements for the _duty_for_on_fraction
/ ChangeDutyCycle plumbing below, nothing else in the pipeline needs to
change.

A small mock GPIO/PWM shim is provided when RPi.GPIO isn't available
(dev machines, CI), matching the pattern used elsewhere in this repo.
"""

import time

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    class _MockPWM:
        def __init__(self, pin, freq):
            pass

        def start(self, duty_cycle):
            pass

        def ChangeDutyCycle(self, duty_cycle):
            pass

        def stop(self):
            pass

    class _GPIO:
        BCM = BOARD = OUT = IN = HIGH = 1
        LOW = 0

        def setmode(self, mode): pass
        def setup(self, pin, mode, initial=1): pass
        def output(self, pin, value): pass
        def cleanup(self): pass
        def setwarnings(self, value): pass

        def PWM(self, pin, freq):
            return _MockPWM(pin, freq)

    GPIO = _GPIO()
    _GPIO_AVAILABLE = False


# Software PWM frequency. RPi.GPIO software PWM gets noticeably jittery
# above ~1kHz on a Pi 4; 400-500Hz is a safe, flicker-free starting point
# for LEDs. Raise cautiously and re-test if you want smoother dimming.
PWM_FREQUENCY_HZ = 500


DEFAULT_CONFIG = {
    "led1_common_anode": False,
    "led1_r": 17,
    "led1_g": 27,
    "led1_b": 22,
    "led2_common_anode": True,
    "led2_r": 23,
    "led2_g": 24,
    "led2_b": 25,
    # ── color calibration ────────────────────────────────────────────────
    # Per-channel multipliers (0.0-1.0) applied on top of the requested
    # color/brightness, per LED. Use these to make LED1 and LED2 *look*
    # the same for "the same" nominal hex color, since they're physically
    # different parts (different die, common-anode vs common-cathode,
    # possibly different forward voltage/luminous output at full current).
    # Start at 1.0 for both and reduce the brighter LED's channels while
    # looking at both LEDs side by side until they visually match at a
    # few reference colors (pure red, pure white are good starting points).
    "led1_calibration": {"r": 1.0, "g": 1.0, "b": 1.0},
    "led2_calibration": {"r": 1.0, "g": 1.0, "b": 1.0},
}

# Compatibility aliases used by the mobile config adapter and older callers.
DEFAULT_PINS = {
    key: value for key, value in DEFAULT_CONFIG.items() if key.endswith(("_r", "_g", "_b"))
}
DEFAULT_COMMON_ANODE = {
    "led1": DEFAULT_CONFIG["led1_common_anode"],
    "led2": DEFAULT_CONFIG["led2_common_anode"],
}


class RGBLed:
    def __init__(
        self,
        r_pin: int,
        g_pin: int,
        b_pin: int,
        common_anode: bool = False,
        name: str = "LED",
        calibration: dict = None,
    ):
        self.pins = {"r": r_pin, "g": g_pin, "b": b_pin}
        self.common_anode = common_anode
        self.name = name
        self.calibration = calibration or {"r": 1.0, "g": 1.0, "b": 1.0}
        self._pwm = {}
        self._setup()

    def _duty_for_on_fraction(self, on_fraction: float) -> float:
        """Map a logical 0.0 (off) .. 1.0 (fully on) value to the PWM
        duty cycle percentage, accounting for common-anode vs
        common-cathode wiring polarity."""
        on_fraction = max(0.0, min(1.0, on_fraction))
        if self.common_anode:
            # LOW = on, so the pin should be HIGH for the "off" fraction
            # of the cycle -- duty cycle (percent time HIGH) is inverted.
            return (1.0 - on_fraction) * 100.0
        return on_fraction * 100.0

    def _setup(self):
        # Initialize pins in their OFF state before starting PWM, same
        # startup-flash avoidance as the original version.
        off_level = GPIO.HIGH if self.common_anode else GPIO.LOW
        for channel, pin in self.pins.items():
            GPIO.setup(pin, GPIO.OUT, initial=off_level)
            pwm = GPIO.PWM(pin, PWM_FREQUENCY_HZ)
            pwm.start(self._duty_for_on_fraction(0.0))
            self._pwm[channel] = pwm

    def _set_channel(self, channel: str, on_fraction: float):
        on_fraction = max(0.0, min(1.0, on_fraction)) * self.calibration.get(channel, 1.0)
        self._pwm[channel].ChangeDutyCycle(self._duty_for_on_fraction(on_fraction))

    def set_color(self, r: float, g: float, b: float, brightness: float = 100.0):
        """Set each channel from a 0.0-1.0 fraction, scaled by an overall
        brightness percentage (0-100)."""
        scale = max(0.0, min(100.0, brightness)) / 100.0
        self._set_channel("r", r * scale)
        self._set_channel("g", g * scale)
        self._set_channel("b", b * scale)

    def set_hex(self, hex_color: str, brightness: float = 100.0):
        value = hex_color.lstrip("#")
        if len(value) != 6:
            raise ValueError(f"Expected six-digit RGB hex color, got {hex_color!r}")
        red = int(value[0:2], 16) / 255.0
        green = int(value[2:4], 16) / 255.0
        blue = int(value[4:6], 16) / 255.0
        self.set_color(red, green, blue, brightness)

    def off(self):
        for channel in self.pins:
            self._set_channel(channel, 0.0)

    def safe_release(self):
        self.off()
        for pwm in self._pwm.values():
            try:
                pwm.stop()
            except Exception:
                pass

    def flash(self, hex_color: str, duration_ms: int, brightness: int = 100) -> tuple:
        """
        Flash for ``duration_ms`` milliseconds at ``brightness`` percent
        (0-100). Timing is unchanged from before -- t_on/t_off still come
        straight from time.time()/time.sleep(), so segmenter.py's video
        sync math doesn't need to change.
        """
        self.set_hex(hex_color, brightness)
        t_on = time.time()
        time.sleep(duration_ms / 1000.0)
        t_off = time.time()
        self.off()
        return t_on, t_off


class LedController:
    def __init__(self, config: dict):
        cfg = {**DEFAULT_CONFIG, **config}

        # Accept the prior application's per-LED key names during migration.
        if "common_anode_led1" in config and "led1_common_anode" not in config:
            cfg["led1_common_anode"] = bool(config["common_anode_led1"])
        if "common_anode_led2" in config and "led2_common_anode" not in config:
            cfg["led2_common_anode"] = bool(config["common_anode_led2"])
        if "common_anode" in config:
            cfg["led1_common_anode"] = bool(config["common_anode"])
            cfg["led2_common_anode"] = bool(config["common_anode"])

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)

        self.led1 = RGBLed(
            r_pin=cfg["led1_r"],
            g_pin=cfg["led1_g"],
            b_pin=cfg["led1_b"],
            common_anode=cfg["led1_common_anode"],
            name="LED1",
            calibration=cfg.get("led1_calibration"),
        )
        self.led2 = RGBLed(
            r_pin=cfg["led2_r"],
            g_pin=cfg["led2_g"],
            b_pin=cfg["led2_b"],
            common_anode=cfg["led2_common_anode"],
            name="LED2",
            calibration=cfg.get("led2_calibration"),
        )

        def wiring(common_anode):
            if common_anode:
                return "common anode  (pin→3.3V, LOW=ON)"
            return "common cathode (pin→GND, HIGH=ON)"

        print(
            f"  [LEDController] LED1  {wiring(cfg['led1_common_anode'])}  "
            f"R={cfg['led1_r']} G={cfg['led1_g']} B={cfg['led1_b']}  PWM@{PWM_FREQUENCY_HZ}Hz"
        )
        print(
            f"  [LEDController] LED2  {wiring(cfg['led2_common_anode'])}  "
            f"R={cfg['led2_r']} G={cfg['led2_g']} B={cfg['led2_b']}  PWM@{PWM_FREQUENCY_HZ}Hz"
        )

    def get_led(self, led_index: int) -> RGBLed:
        if led_index == 1:
            return self.led1
        if led_index == 2:
            return self.led2
        raise ValueError(f"Invalid LED index: {led_index}. Must be 1 or 2.")

    def all_off(self):
        self.led1.off()
        self.led2.off()

    def cleanup(self):
        self.led1.safe_release()
        self.led2.safe_release()
        GPIO.cleanup()
        print("  [LEDController] GPIO cleaned up.")
