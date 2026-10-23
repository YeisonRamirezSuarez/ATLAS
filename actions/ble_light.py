"""Bluetooth LE LED controller: BanlanX SP621E "Mini SPI RGB Controller" (BanlanX v2 protocol).

Protocol taken from UniLED (github.com/monty68/uniled, lib/ble/banlanx2.py, MIT License,
Copyright (c) 2022 Phil M). Characteristic ffe1 of service ffe0 carries both the commands
and the status notifications. A change counts as verified only when the controller's own
status report shows it."""
import asyncio

CHAR             = "0000ffe1-0000-1000-8000-00805f9b34fb"
_MANUFACTURER_ID = (20563, 5053)
_SP621E_DATA     = (0x0D, 0x16)   # first byte of the SP621E manufacturer data
EFFECT_SOLID     = 0xBE
STATE_QUERY      = bytes([0xA0, 0x70, 0x00])
SETTLE_SECONDS   = 0.3            # hardware knob: time the controller needs before it reports the new state

# The color names people say most; anything else goes to Qt's SVG/CSS color names or #RRGGBB.
_ES = {"rojo": "red", "verde": "green", "azul": "blue", "morado": "purple", "violeta": "violet",
       "lila": "plum", "amarillo": "yellow", "naranja": "orange", "blanco": "white", "rosa": "pink",
       "rosado": "pink", "cian": "cyan", "turquesa": "turquoise", "dorado": "gold"}


def power_cmd(on: bool) -> bytes:
    return bytes([0xA0, 0x62, 0x01, 0x01 if on else 0x00])


def effect_cmd(effect: int) -> bytes:
    return bytes([0xA0, 0x63, 0x01, effect])


def color_cmd(r: int, g: int, b: int, level: int = 0xFF) -> bytes:
    return bytes([0xA0, 0x69, 0x04, r, g, b, level])


def parse_rgb(color: str) -> tuple[int, int, int] | None:
    from PyQt6.QtGui import QColor
    name = color.strip().lower()
    c = QColor(_ES.get(name, name))
    return (c.red(), c.green(), c.blue()) if name and c.isValid() else None


class StatusAssembler:
    """Rebuilds a status message. Header: 53 43 <packet no> <message length> <payload length>;
    a message may span several packets. Notifications without the header are the payload itself."""

    def __init__(self):
        self.buf, self.want, self.packet = b"", 0, 0

    def feed(self, data: bytes) -> bytes | None:
        data = bytes(data)
        if len(data) <= 5 or data[:2] != b"\x53\x43":
            return data if len(data) >= 10 else None
        packet, total, size = data[2], data[3], data[4]
        if packet == 1:
            self.buf, self.want = b"", total
        elif packet != self.packet + 1 or total != self.want:
            self.buf, self.packet = b"", 0   # out of sequence: drop it, the next query starts over
            return None
        self.packet = packet
        self.buf += data[5:5 + size]
        return self.buf if len(self.buf) == self.want else None


def decode(payload: bytes) -> dict:
    return {"on": payload[0] == 0x01, "effect": payload[2], "brightness": payload[4],
            "rgb": tuple(payload[7:10])}


def _is_sp621e(adv, name: str | None) -> bool:
    for mid, data in (getattr(adv, "manufacturer_data", None) or {}).items():
        if mid in _MANUFACTURER_ID and data[:1] and data[0] in _SP621E_DATA:
            return True
    return "sp621e" in (name or getattr(adv, "local_name", None) or "").lower()


async def _scan(timeout: float = 8.0) -> list:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [dev for dev, adv in found.values() if _is_sp621e(adv, dev.name)]


async def _find(address: str | None):
    if address:
        from bleak import BleakScanner
        return await BleakScanner.find_device_by_address(address, timeout=8.0)
    devices = await _scan()
    return devices[0] if devices else None


async def _session(device, commands: list[bytes], timeout: float = 5.0) -> dict | None:
    """Sends the commands, then asks for the state. Returns the decoded state, or None if none came."""
    from bleak import BleakClient
    assembler, got, last = StatusAssembler(), asyncio.Event(), {}

    def on_notify(_sender, data):
        payload = assembler.feed(data)
        if payload:
            last["payload"] = payload
            got.set()

    async with BleakClient(device) as client:
        await client.start_notify(CHAR, on_notify)
        for cmd in commands:
            await client.write_gatt_char(CHAR, cmd, response=True)
        if commands:
            await asyncio.sleep(SETTLE_SECONDS)
        got.clear()
        last.clear()
        await client.write_gatt_char(CHAR, STATE_QUERY, response=True)
        try:
            await asyncio.wait_for(got.wait(), timeout)
        except asyncio.TimeoutError:
            return None
    return decode(last["payload"])


async def _run(action: str, address: str | None, rgb) -> str:
    if action == "scan":
        devices = await _scan()
        if not devices:
            return "Could not find any SP621E controller. Check it is powered and not connected to the phone app."
        return "SP621E controllers found: " + ", ".join(f"{d.name or 'unnamed'} ({d.address})" for d in devices)

    device = await _find(address)
    if device is None:
        return ("Could not find the SP621E controller over Bluetooth"
                + (f" at {address}" if address else "")
                + ". Check it is powered, in range, and not connected to the phone app.")
    label = f"{device.name or 'SP621E'} ({device.address})"

    if action == "color":
        commands = [power_cmd(True), effect_cmd(EFFECT_SOLID), color_cmd(*rgb)]
    elif action in ("on", "off"):
        commands = [power_cmd(action == "on")]
    else:
        commands = []

    state = await _session(device, commands)
    if state is None:
        if not commands:
            return f"Could not read the state of {label}: the controller did not answer."
        return f"Commands sent to {label}, but the controller did not report its state. NOT VERIFIED."

    report = (f"{label}: {'on' if state['on'] else 'off'}, effect 0x{state['effect']:02X}, "
              f"RGB{state['rgb']}, brightness {state['brightness']}")
    if action == "status":
        return f"State read from the controller: {report}."
    expected_on = action != "off"
    ok = state["on"] == expected_on and (action != "color" or (state["rgb"] == tuple(rgb)
                                                               and state["effect"] == EFFECT_SOLID))
    if ok:
        return f"Verified by the controller's status report: {report}."
    return f"Commands sent, but the controller reports a different state: {report}. NOT VERIFIED."


def ble_light(parameters: dict, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "color").strip().lower()
    if action not in ("color", "on", "off", "status", "scan"):
        return f"Unknown ble_light action '{action}'. Use color, on, off, status or scan."
    rgb = None
    if action == "color":
        rgb = parse_rgb(str(p.get("color") or ""))
        if rgb is None:
            return f"Could not understand the color '{p.get('color')}'. Use a color name or #RRGGBB."
    try:
        return asyncio.run(_run(action, (p.get("address") or "").strip() or None, rgb))
    except ImportError:
        return "Could not use Bluetooth: the 'bleak' package is not installed."
    except Exception as e:
        return f"Could not control the SP621E controller: {e}"
