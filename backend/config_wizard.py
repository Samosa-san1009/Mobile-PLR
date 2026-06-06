"""
config_wizard.py
----------------
Interactive terminal configuration wizard.
Supports three flash modes:
  1. Dual        — both LEDs same color, same time
  2. Left→Right  — LED1 flashes, pause, LED2 flashes  (one round = L then R)
  3. Right→Left  — LED2 flashes, pause, LED1 flashes  (one round = R then L)
"""

import re
import os

VALID_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

COLOR_TABLE = {
    "#FF0000": "Red",
    "#00FF00": "Green",
    "#0000FF": "Blue",
    "#FFFF00": "Yellow",
    "#FF00FF": "Magenta",
    "#00FFFF": "Cyan",
    "#FFFFFF": "White",
}

MODES = {
    "1": "dual",
    "2": "left_to_right",
    "3": "right_to_left",
}

MODE_LABELS = {
    "dual":          "Dual          (both LEDs same color, simultaneously)",
    "left_to_right": "Left → Right  (left eye first, pause, right eye)",
    "right_to_left": "Right → Left  (right eye first, pause, left eye)",
}


# ── low-level helpers ─────────────────────────────────────────────────────────

def _hr(char="─", width=58):
    print(char * width)

def _section(title: str):
    print()
    _hr()
    print(f"  {title}")
    _hr()

def _prompt(label: str, default=None, validator=None) -> str:
    hint = f"  [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {label}{hint}: ").strip()
        if raw == "" and default is not None:
            raw = str(default)
        if not raw:
            print("  ✗  Please enter a value.")
            continue
        if validator:
            err = validator(raw)
            if err:
                print(f"  ✗  {err}")
                continue
        return raw

def _prompt_int(label: str, default=None, min_val=None, max_val=None) -> int:
    def validate(v):
        try:
            n = int(v)
        except ValueError:
            return "Please enter a whole number."
        if min_val is not None and n < min_val:
            return f"Must be at least {min_val}."
        if max_val is not None and n > max_val:
            return f"Must be at most {max_val}."
        return None
    return int(_prompt(label, default=default, validator=validate))

def _prompt_hex(label: str) -> str:
    _print_color_table()
    def validate(v):
        if not VALID_HEX.match(v):
            return "Enter a valid hex color like #FF0000 (include the #)."
        return None
    return _prompt(label, validator=validate).upper()

def _prompt_bool(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = _prompt(f"{label} ({hint})", default="y" if default else "n").lower()
    return raw.startswith("y")

def _prompt_gpio_pin(label: str) -> int:
    return _prompt_int(label, min_val=2, max_val=27)

def _print_color_table():
    print()
    print("  Available HIGH/LOW colors:")
    for hex_code, name in COLOR_TABLE.items():
        r = int(hex_code[1:3], 16) >= 128
        g = int(hex_code[3:5], 16) >= 128
        b = int(hex_code[5:7], 16) >= 128
        ch = f"R={'ON ' if r else 'OFF'} G={'ON ' if g else 'OFF'} B={'ON ' if b else 'OFF'}"
        print(f"    {hex_code}  {name:<10}  {ch}")
    print()


# ── hardware config (shared by all modes) ─────────────────────────────────────

def _collect_gpio_pins() -> dict:
    _section("GPIO Pin Assignment  (BCM numbering)")
    print("  Left LED  = LED 1    Right LED = LED 2\n")
    pins = {}
    for led_n, side in ((1, "Left "), (2, "Right")):
        print(f"  LED {led_n}  ({side}):")
        pins[f"led{led_n}_r"] = _prompt_gpio_pin("    R pin")
        pins[f"led{led_n}_g"] = _prompt_gpio_pin("    G pin")
        pins[f"led{led_n}_b"] = _prompt_gpio_pin("    B pin")
        print()
    return pins

def _collect_led_type() -> bool:
    _section("LED Wiring Type")
    print("  Common cathode : common pin → GND   (most common)")
    print("  Common anode   : common pin → 3.3V/5V")
    print()
    choice = _prompt(
        "LED type  [1 = common cathode,  2 = common anode]",
        default="1",
        validator=lambda v: None if v in ("1", "2") else "Enter 1 or 2.",
    )
    return choice == "2"

def _collect_camera_config() -> dict:
    _section("Camera Settings")
    device  = _prompt_int("  Camera device index (usually 0)", default=0, min_val=0)
    out_dir = _prompt("  Recording output folder", default="recordings")
    os.makedirs(out_dir, exist_ok=True)
    return {"device_index": device, "output_dir": out_dir}

def _collect_mode() -> str:
    _section("Flash Mode")
    for key, mode in MODES.items():
        print(f"  {key}.  {MODE_LABELS[mode]}")
    print()
    choice = _prompt(
        "Select mode",
        default="1",
        validator=lambda v: None if v in MODES else "Enter 1, 2, or 3.",
    )
    return MODES[choice]


# ── mode-specific schedule collectors ────────────────────────────────────────

def _collect_dual_schedule() -> dict:
    """
    Both LEDs fire the same color at the same time.
    Returns:
      {
        "flashes": [{"hex": str, "duration_ms": int}, ...],
        "gap_ms":  int,
      }
    """
    _section("Dual Mode — Flash Schedule")
    print("  Both LEDs will fire together with the same color.\n")

    flashes = []
    for i in range(1, 4):          # default 3
        print(f"  Flash {i} (default):")
        flashes.append(_collect_one_flash(i))

    while True:
        add = _prompt_bool(f"  Add another flash? (currently {len(flashes)})", default=False)
        if not add:
            break
        flashes.append(_collect_one_flash(len(flashes) + 1))

    gap_ms = _collect_gap_between_rounds("flashes")
    return {"flashes": flashes, "gap_ms": gap_ms}


def _collect_sequential_schedule(mode: str) -> dict:
    """
    For left_to_right and right_to_left.
    One "round" = first side flashes, inner pause, second side flashes.
    Returns:
      {
        "rounds":         int,
        "hex":            str,   # same color used for both sides every round
        "duration_ms":    int,   # flash duration per side
        "inner_pause_ms": int,   # pause between left and right within a round
        "gap_ms":         int,   # gap between rounds
      }
    """
    if mode == "left_to_right":
        first,  second = "Left (LED 1)", "Right (LED 2)"
    else:
        first, second = "Right (LED 2)", "Left (LED 1)"

    _section(f"{'Left → Right' if mode == 'left_to_right' else 'Right → Left'} Mode — Schedule")
    print(f"  Each round:  {first} flashes  →  pause  →  {second} flashes\n")

    rounds = _prompt_int("  Number of rounds", default=3, min_val=1)
    print()

    hex_color    = _prompt_hex("  Flash color (same for both sides)")
    duration_ms  = _prompt_int("  Flash duration per side (ms)", default=300, min_val=50)
    inner_pause  = _prompt_int(
        f"  Pause between {first.split()[0]} and {second.split()[0]} (ms)",
        default=500, min_val=50,
    )
    gap_ms       = _collect_gap_between_rounds("rounds")

    return {
        "rounds":         rounds,
        "hex":            hex_color,
        "duration_ms":    duration_ms,
        "inner_pause_ms": inner_pause,
        "gap_ms":         gap_ms,
    }


def _collect_one_flash(index: int) -> dict:
    hex_color   = _prompt_hex(f"    Flash {index} color (hex)")
    duration_ms = _prompt_int(f"    Flash {index} duration (ms)", default=300, min_val=50)
    print()
    return {"hex": hex_color, "duration_ms": duration_ms}


def _collect_gap_between_rounds(unit: str = "rounds") -> int:
    return _prompt_int(
        f"  Gap between {unit} — both LEDs off (ms)",
        default=800,
        min_val=100,
    )


# ── summary printers ──────────────────────────────────────────────────────────

def _print_summary(config: dict):
    _section("Session Summary")

    led_type = "Common anode" if config["common_anode"] else "Common cathode"
    mode     = config["mode"]

    print(f"  Mode          : {MODE_LABELS[mode]}")
    print(f"  LED wiring    : {led_type}")
    print()

    for led_n, side in ((1, "Left"), (2, "Right")):
        pins = (config[f"led{led_n}_r"], config[f"led{led_n}_g"], config[f"led{led_n}_b"])
        print(f"  LED {led_n} ({side:<5}) : R={pins[0]}  G={pins[1]}  B={pins[2]}")
    print()

    sched = config["schedule"]

    if mode == "dual":
        print(f"  Flashes:")
        for i, f in enumerate(sched["flashes"], 1):
            print(f"    {i}.  {f['hex']}   {f['duration_ms']} ms")
        print(f"  Gap between flashes : {sched['gap_ms']} ms")

    else:
        arrow = "Left → Right" if mode == "left_to_right" else "Right → Left"
        print(f"  Rounds        : {sched['rounds']}")
        print(f"  Color         : {sched['hex']}")
        print(f"  Flash duration: {sched['duration_ms']} ms per side")
        print(f"  Inner pause   : {sched['inner_pause_ms']} ms  ({arrow})")
        print(f"  Gap between rounds: {sched['gap_ms']} ms")

    print()
    print(f"  Camera device : /dev/video{config['camera']['device_index']}")
    print(f"  Output folder : {config['camera']['output_dir']}/")
    _hr()


# ── main entry point ──────────────────────────────────────────────────────────

def run_wizard() -> dict:
    """
    Run the full interactive wizard.
    Returns a config dict ready to pass to the Orchestrator.
    """
    print()
    print("╔════════════════════════════════════════════════════════╗")
    print("║        PLR Pupillometry — Session Configuration        ║")
    print("╚════════════════════════════════════════════════════════╝")

    gpio_pins    = _collect_gpio_pins()
    common_anode = _collect_led_type()
    mode         = _collect_mode()

    if mode == "dual":
        schedule = _collect_dual_schedule()
    else:
        schedule = _collect_sequential_schedule(mode)

    camera_cfg = _collect_camera_config()

    config = {
        **gpio_pins,
        "common_anode": common_anode,
        "mode":         mode,
        "schedule":     schedule,
        "camera":       camera_cfg,
    }

    _print_summary(config)

    confirmed = _prompt_bool("Start session now?", default=True)
    if not confirmed:
        print("\n  Session cancelled. Exiting.\n")
        raise SystemExit(0)

    print("\n  Starting session...\n")
    return config
