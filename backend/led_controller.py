"""
led_controller.py
-----------------
Controls two 4-pin RGB LEDs via RPi.GPIO software PWM.

  Common cathode: duty = intensity        (HIGH side modulated)
  Common anode  : duty = 100 - intensity  (LOW side modulated, inverted)

PWM frequency is kept low (200 Hz) to minimise Pi 4 CPU usage
on the cooler-less board — still well above the eye's flicker
fusion threshold.
"""

import time

try:
    import RPi.GPIO as GPIO
    _REAL_GPIO = True
except ImportError:
    # Mock for development on non-Pi machines
    class _PWMStub:
        def __init__(self, *_a, **_kw): pass
        def start(self, *_a, **_kw): pass
        def ChangeDutyCycle(self, *_a, **_kw): pass
        def stop(self): pass

    class _GPIO:
        BCM = BOARD = OUT = IN = HIGH = 1
        LOW = 0
        def setmode(self, m): pass
        def setup(self, pin, mode): pass
        def output(self, pin, val): pass
        def cleanup(self): pass
        def setwarnings(self, v): pass
        def PWM(self, pin, freq): return _PWMStub()
    GPIO = _GPIO()
    _REAL_GPIO = False


# ─────────────────────────────────────────────────────────────────────────────
#  GPIO PIN PLACEHOLDERS
#  Override these from server config / env. Two RGB LEDs, BCM numbering.
#  Avoid GPIO 0,1 (I2C) and 14,15 (UART).
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PINS = {
    "led1_r": 17,   # ← placeholder — set in server config per device
    "led1_g": 27,   # ← placeholder
    "led1_b": 22,   # ← placeholder
    "led2_r": 23,   # ← placeholder
    "led2_g": 24,   # ← placeholder
    "led2_b": 25,   # ← placeholder
}

# Wiring type per LED — can differ between the two LEDs on the same board.
DEFAULT_COMMON_ANODE = {
    "led1": False,  # ← placeholder: True = common anode, False = common cathode
    "led2": False,  # ← placeholder
}

PWM_FREQ_HZ = 200   # low CPU, no visible flicker


class RGBLed:
    """Single RGB LED with software-PWM intensity control."""

    def __init__(self, r_pin: int, g_pin: int, b_pin: int,
                 common_anode: bool = False, name: str = "LED"):
        self.pins = {"r": r_pin, "g": g_pin, "b": b_pin}
        self.common_anode = common_anode
        self.name = name

        self._pwm = {}
        for ch, pin in self.pins.items():
            GPIO.setup(pin, GPIO.OUT)
            p = GPIO.PWM(pin, PWM_FREQ_HZ)
            p.start(self._duty_off())
            self._pwm[ch] = p

    def _duty_on(self, intensity_pct: float) -> float:
        intensity_pct = max(0.0, min(100.0, intensity_pct))
        return (100.0 - intensity_pct) if self.common_anode else intensity_pct

    def _duty_off(self) -> float:
        return 100.0 if self.common_anode else 0.0

    def set_hex(self, hex_color: str, intensity_pct: float = 100.0):
        """
        Drive each channel at intensity_pct duty if its hex byte >= 128.
        Sub-128 channels are driven off.
        """
        hex_color = hex_color.lstrip("#")
        bytes_ = (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
        for ch, val in zip(("r", "g", "b"), bytes_):
            duty = self._duty_on(intensity_pct) if val >= 128 else self._duty_off()
            self._pwm[ch].ChangeDutyCycle(duty)

    def off(self):
        for p in self._pwm.values():
            p.ChangeDutyCycle(self._duty_off())

    def flash(self, hex_color: str, duration_ms: int,
              intensity_pct: float = 100.0) -> tuple:
        """Returns (t_on, t_off) Unix timestamps."""
        self.set_hex(hex_color, intensity_pct)
        t_on = time.time()
        time.sleep(duration_ms / 1000.0)
        t_off = time.time()
        self.off()
        return t_on, t_off

    def stop_pwm(self):
        for p in self._pwm.values():
            try:
                p.stop()
            except Exception:
                pass


class LedController:
    """Manages both RGB LEDs."""

    def __init__(self, config: dict):
        """
        Expected config keys (all optional — fall back to placeholders):
            led1_r, led1_g, led1_b, led2_r, led2_g, led2_b : int (BCM)
            common_anode      : bool                  (applies to both LEDs)
              OR
            common_anode_led1 : bool
            common_anode_led2 : bool                  (per-LED override)
        """
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)

        pins = {k: config.get(k, DEFAULT_PINS[k]) for k in DEFAULT_PINS}

        if "common_anode_led1" in config or "common_anode_led2" in config:
            ca1 = config.get("common_anode_led1", DEFAULT_COMMON_ANODE["led1"])
            ca2 = config.get("common_anode_led2", DEFAULT_COMMON_ANODE["led2"])
        else:
            shared = config.get("common_anode", False)
            ca1 = ca2 = shared

        self.led1 = RGBLed(pins["led1_r"], pins["led1_g"], pins["led1_b"],
                           common_anode=ca1, name="LED1")
        self.led2 = RGBLed(pins["led2_r"], pins["led2_g"], pins["led2_b"],
                           common_anode=ca2, name="LED2")

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
        self.all_off()
        self.led1.stop_pwm()
        self.led2.stop_pwm()
        GPIO.cleanup()
