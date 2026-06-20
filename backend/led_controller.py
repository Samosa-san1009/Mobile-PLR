"""
led_controller.py
-----------------
Controls two 4-pin RGB LEDs via GPIO HIGH/LOW.

CONFIRMED HARDWARE WIRING:
  LED1 (left)  — common CATHODE → common pin to GND   → HIGH = ON
  LED2 (right) — common ANODE   → common pin to 3.3V  → LOW  = ON

Each LED has its own common_anode flag so they can be wired differently.
Pins are initialized directly in their OFF state to avoid startup flashes.
"""

import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    class _GPIO:
        BCM = BOARD = OUT = IN = HIGH = 1
        LOW = 0

        def setmode(self, mode): pass
        def setup(self, pin, mode, initial=1): pass
        def output(self, pin, value): pass
        def cleanup(self): pass
        def setwarnings(self, value): pass

    GPIO = _GPIO()


DEFAULT_CONFIG = {
    "led1_common_anode": False,
    "led1_r": 17,
    "led1_g": 27,
    "led1_b": 22,
    "led2_common_anode": True,
    "led2_r": 23,
    "led2_g": 24,
    "led2_b": 25,
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
    ):
        self.pins = {"r": r_pin, "g": g_pin, "b": b_pin}
        self.common_anode = common_anode
        self.name = name
        self._setup()

    def _on_level(self):
        return GPIO.LOW if self.common_anode else GPIO.HIGH

    def _off_level(self):
        return GPIO.HIGH if self.common_anode else GPIO.LOW

    def _setup(self):
        off = self._off_level()
        for pin in self.pins.values():
            GPIO.setup(pin, GPIO.OUT, initial=off)

    def set_color(self, r: bool, g: bool, b: bool):
        GPIO.output(self.pins["r"], self._on_level() if r else self._off_level())
        GPIO.output(self.pins["g"], self._on_level() if g else self._off_level())
        GPIO.output(self.pins["b"], self._on_level() if b else self._off_level())

    def set_hex(self, hex_color: str):
        value = hex_color.lstrip("#")
        if len(value) != 6:
            raise ValueError(f"Expected six-digit RGB hex color, got {hex_color!r}")
        red = int(value[0:2], 16) >= 128
        green = int(value[2:4], 16) >= 128
        blue = int(value[4:6], 16) >= 128
        self.set_color(red, green, blue)

    def off(self):
        off = self._off_level()
        for pin in self.pins.values():
            GPIO.output(pin, off)

    def safe_release(self):
        self.off()

    def flash(self, hex_color: str, duration_ms: int, brightness: int = 100) -> tuple:
        """
        Flash for ``duration_ms`` milliseconds.

        ``brightness`` is accepted for API compatibility but ignored because
        this timing-safe controller uses direct HIGH/LOW output, not PWM.
        """
        self.set_hex(hex_color)
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
        )
        self.led2 = RGBLed(
            r_pin=cfg["led2_r"],
            g_pin=cfg["led2_g"],
            b_pin=cfg["led2_b"],
            common_anode=cfg["led2_common_anode"],
            name="LED2",
        )

        def wiring(common_anode):
            if common_anode:
                return "common anode  (pin→3.3V, LOW=ON)"
            return "common cathode (pin→GND, HIGH=ON)"

        print(
            f"  [LEDController] LED1  {wiring(cfg['led1_common_anode'])}  "
            f"R={cfg['led1_r']} G={cfg['led1_g']} B={cfg['led1_b']}"
        )
        print(
            f"  [LEDController] LED2  {wiring(cfg['led2_common_anode'])}  "
            f"R={cfg['led2_r']} G={cfg['led2_g']} B={cfg['led2_b']}"
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
