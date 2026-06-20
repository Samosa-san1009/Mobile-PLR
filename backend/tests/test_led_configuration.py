import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import led_controller
from config_adapter import adapt


class RecordingGPIO:
    BCM = 11
    OUT = 1
    HIGH = 1
    LOW = 0

    def __init__(self):
        self.setup_calls = []
        self.output_calls = []
        self.cleaned = False

    def setwarnings(self, value): pass
    def setmode(self, mode): pass

    def setup(self, pin, mode, initial=None):
        self.setup_calls.append((pin, mode, initial))

    def output(self, pin, value):
        self.output_calls.append((pin, value))

    def cleanup(self):
        self.cleaned = True


class LedConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.original_gpio = led_controller.GPIO
        self.gpio = RecordingGPIO()
        led_controller.GPIO = self.gpio

    def tearDown(self):
        led_controller.GPIO = self.original_gpio

    def test_mixed_wiring_initializes_every_pin_off(self):
        controller = led_controller.LedController({})

        led1_initials = [call[2] for call in self.gpio.setup_calls[:3]]
        led2_initials = [call[2] for call in self.gpio.setup_calls[3:]]
        self.assertEqual(led1_initials, [self.gpio.LOW] * 3)
        self.assertEqual(led2_initials, [self.gpio.HIGH] * 3)

        controller.cleanup()
        self.assertTrue(self.gpio.cleaned)

    def test_prior_per_led_key_names_remain_supported(self):
        controller = led_controller.LedController({
            "common_anode_led1": True,
            "common_anode_led2": False,
        })
        self.assertTrue(controller.led1.common_anode)
        self.assertFalse(controller.led2.common_anode)

    def test_mobile_adapter_uses_confirmed_default_wiring(self):
        config = adapt({
            "eye": "Left",
            "color": "White",
            "iterations": 1,
            "duration": 1,
            "delay": 3,
            "intensity": 50,
        })
        self.assertFalse(config["led1_common_anode"])
        self.assertTrue(config["led2_common_anode"])


if __name__ == "__main__":
    unittest.main()
