"""SP621E adapter checks with a fake Bluetooth stack: no adapter or controller needed.

Run:  python -m unittest tests.test_ble_light
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import ble_light as bl


def _packet(payload: bytes, number: int = 1, total: int | None = None) -> bytes:
    return bytes([0x53, 0x43, number, total or len(payload), len(payload)]) + payload


class FakeController:
    """Applies the v2 commands it receives and answers the state query like the real controller."""

    def __init__(self, answers=True, split=False):
        self.on, self.effect, self.rgb = 0, 0x0E, (255, 0, 0)
        self.answers, self.split, self.writes, self.notify = answers, split, [], None

    def state(self) -> bytes:
        return bytes([self.on, 0, self.effect, 2, 0xFF, 10, 30, *self.rgb, 1]) + bytes(12)

    # BleakClient interface
    def __call__(self, device):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start_notify(self, char, callback):
        self.notify = callback

    async def write_gatt_char(self, char, data, response=True):
        self.writes.append(bytes(data))
        cmd = data[1]
        if cmd == 0x62:
            self.on = data[3]
        elif cmd == 0x63:
            self.effect = data[3]
        elif cmd == 0x69:
            self.rgb = tuple(data[3:6])
        elif cmd == 0x70 and self.answers:
            s = self.state()
            if self.split:
                self.notify(None, _packet(s[:10], 1, len(s)))
                self.notify(None, _packet(s[10:], 2, len(s)))
            else:
                self.notify(None, _packet(s))


def _fake_bleak(controller, devices):
    async def discover(timeout=8.0, return_adv=True):
        return {d.address: (d, adv) for d, adv in devices}

    async def find_device_by_address(address, timeout=8.0):
        return next((d for d, _ in devices if d.address == address), None)

    scanner = types.SimpleNamespace(discover=discover, find_device_by_address=find_device_by_address)
    return types.SimpleNamespace(BleakScanner=scanner, BleakClient=controller)


def _sp621e(address="AA:BB:CC:DD:EE:01"):
    dev = types.SimpleNamespace(address=address, name="SP621E")
    adv = types.SimpleNamespace(manufacturer_data={20563: b"\x0d\x10"}, local_name="SP621E")
    return dev, adv


class BleLightTests(unittest.TestCase):
    def run_tool(self, params, controller, devices):
        with mock.patch.dict(sys.modules, {"bleak": _fake_bleak(controller, devices)}), \
             mock.patch.object(bl, "SETTLE_SECONDS", 0):
            return bl.ble_light(params)

    def test_command_bytes_match_uniled(self):
        self.assertEqual(bl.power_cmd(True), bytes.fromhex("a0620101"))
        self.assertEqual(bl.effect_cmd(bl.EFFECT_SOLID), bytes.fromhex("a06301be"))
        self.assertEqual(bl.color_cmd(128, 0, 128), bytes.fromhex("a06904800080ff"))
        self.assertEqual(bl.STATE_QUERY, bytes.fromhex("a07000"))

    def test_color_names(self):
        self.assertEqual(bl.parse_rgb("morado"), (128, 0, 128))
        self.assertEqual(bl.parse_rgb("purple"), (128, 0, 128))
        self.assertEqual(bl.parse_rgb("#8000ff"), (128, 0, 255))
        self.assertIsNone(bl.parse_rgb("not-a-color"))

    def test_multi_packet_status(self):
        a = bl.StatusAssembler()
        payload = bytes(range(23))
        self.assertIsNone(a.feed(_packet(payload[:10], 1, 23)))
        self.assertEqual(a.feed(_packet(payload[10:], 2, 23)), payload)
        self.assertIsNone(a.feed(_packet(payload[10:], 3, 23)))   # out of sequence

    def test_purple_is_verified_by_status(self):
        ctl = FakeController(split=True)
        out = self.run_tool({"action": "color", "color": "morado"}, ctl, [_sp621e()])
        self.assertTrue(out.startswith("Verified"), out)
        self.assertIn("RGB(128, 0, 128)", out)
        self.assertEqual(ctl.writes[:3], [bl.power_cmd(True), bl.effect_cmd(0xBE), bl.color_cmd(128, 0, 128)])

    def test_no_answer_is_not_verified(self):
        out = self.run_tool({"action": "color", "color": "purple"}, FakeController(answers=False), [_sp621e()])
        self.assertIn("NOT VERIFIED", out)
        self.assertFalse(out.startswith("Verified"))

    def test_device_not_found_is_a_failure(self):
        out = self.run_tool({"action": "color", "color": "purple"}, FakeController(), [])
        self.assertTrue(out.startswith("Could not find"), out)

    def test_other_ble_devices_ignored(self):
        other = (types.SimpleNamespace(address="11:22", name="Headphones"),
                 types.SimpleNamespace(manufacturer_data={76: b"\x0d"}, local_name="Headphones"))
        out = self.run_tool({"action": "scan"}, FakeController(), [other, _sp621e()])
        self.assertIn("AA:BB:CC:DD:EE:01", out)
        self.assertNotIn("Headphones", out)


if __name__ == "__main__":
    unittest.main()
