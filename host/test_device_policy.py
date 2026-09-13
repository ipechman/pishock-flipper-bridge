import copy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
import unittest

from device_policy import (MAX_COMMAND_BYTES, MAX_REGISTRATION_BYTES, MAX_SHARES,
                           PolicyError, admit, parse_registration,
                           snapshot_from_registration)
from serial_mirror import HubCommand, STOP


IDENTITY = SimpleNamespace(hub_id=4100, owner_id=9000, shocker_id=1234, channel=0)
CODE = "EXAMPLE_PRIVATE_SHARE_CODE"


def registration(**changes):
    result = {"r": True, "t": "R", "c": 4100, "oid": 9000, "cl": True,
              "s": [{"id": 1234, "t": 1, "p": False}], "p": {}}
    result.update(changes)
    return result


def share(**changes):
    result = {"c": CODE, "s": 1234, "u": 87654, "p": False,
              "d": 2, "i": 15, "z": True, "v": True, "b": True}
    result.update(changes)
    return result


def operation(**changes):
    result = {"id": 1234, "m": "s", "i": 10, "d": 500, "r": True}
    result.update(changes)
    return json.dumps(result).encode()


class RegistrationTests(unittest.TestCase):
    def test_empty_object_and_poll_response(self):
        for kind in ("R", "P"):
            snapshot = parse_registration(json.dumps(registration(t=kind)).encode(), IDENTITY)
            self.assertFalse(snapshot.shocker_paused)
            self.assertEqual(dict(snapshot.shares), {})

    def test_fail_closed_identity_status_and_selected_model(self):
        for patch in ({"r": 1}, {"r": False}, {"cl": 1}, {"cl": False},
                      {"t": "unknown"}, {"c": 4101}, {"c": True}, {"oid": 1},
                      {"s": []}, {"s": [{"id": 1234, "t": True, "p": False}]},
                      {"s": [{"id": 1234, "t": 1, "p": 0}]}):
            with self.subTest(patch=patch), self.assertRaises(PolicyError):
                snapshot_from_registration(registration(**patch), IDENTITY)

    def test_object_values_only_and_malformed_entries(self):
        invalid = [[], [share()], {"x": [share()]}, {"x": None}, {"x": {}},
                   {"x": share(d=True)}, {"x": share(d=0)}, {"x": share(i=101)},
                   {"x": share(p=0)}, {"x": share(z=1)}, {"x": share(s=999)},
                   {"x": share(c="")}, {"x": share(c="bad\ncode")},
                   {"x": share(), "y": share()}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                snapshot_from_registration(registration(p=value), IDENTITY)

    def test_duplicate_keys_and_oversize(self):
        raw = json.dumps(registration()).encode()
        for value in (raw[:-1] + b',"r":true}', b" " * (MAX_REGISTRATION_BYTES + 1),
                      b'{"r":NaN}', b'{"r":1e999}', b"{" * 1000):
            with self.assertRaises(PolicyError):
                parse_registration(value, IDENTITY)
        with self.assertRaises(PolicyError):
            snapshot_from_registration(registration(p={str(i): share(c=str(i))
                                                        for i in range(MAX_SHARES + 1)}), IDENTITY)

    def test_immutable_snapshot_and_redacted_diagnostics(self):
        data = registration(p={"any-key": share()}, k="SECRET_PAIRING_KEY")
        snapshot = snapshot_from_registration(data, IDENTITY)
        data["p"]["any-key"]["p"] = True
        self.assertFalse(snapshot.shares[CODE.encode()].paused)
        with self.assertRaises(TypeError):
            snapshot.shares[CODE.encode()] = None
        with self.assertRaises(FrozenInstanceError):
            snapshot.shocker_paused = True
        self.assertNotIn(CODE, repr(snapshot))
        self.assertNotIn("SECRET_PAIRING_KEY", repr(snapshot))
        try:
            parse_registration(b'{"' + CODE.encode() + b'":}', IDENTITY)
        except PolicyError as error:
            self.assertNotIn(CODE, str(error))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = snapshot_from_registration(registration(p={"arbitrary": share()}), IDENTITY)
        self.owner = b"c4100-ops"
        self.shared = b"c4100-sops-" + CODE.encode()

    def test_owner_modes_and_repeat_flag(self):
        for mode, expected in (("s", "shock"), ("v", "vibrate"), ("b", "beep")):
            decision = admit(self.owner, operation(m=mode, r=False), self.snapshot, IDENTITY)
            self.assertEqual(decision.command, HubCommand(expected, 0 if mode == 'b' else 10, 500))
            self.assertFalse(decision.repeat)
            self.assertFalse(decision.replace)

    def test_share_clamps_without_extending(self):
        decision = admit(self.shared, operation(i=100, d=9999), self.snapshot, IDENTITY)
        self.assertEqual(decision.command, HubCommand("shock", 15, 2000))
        decision = admit(self.shared, operation(i=1, d=100), self.snapshot, IDENTITY)
        self.assertEqual(decision.command, HubCommand("shock", 1, 100))

    def test_pause_and_disabled_modes(self):
        for change in ({"p": True}, {"z": False}):
            snapshot = snapshot_from_registration(registration(p={"a": share(**change)}), IDENTITY)
            self.assertIsNone(admit(self.shared, operation(), snapshot, IDENTITY))
        snapshot = snapshot_from_registration(registration(s=[{"id": 1234, "t": 1, "p": True}],
                                                              p={"a": share()}), IDENTITY)
        for channel in (self.owner, self.shared):
            self.assertIsNone(admit(channel, operation(), snapshot, IDENTITY))
            self.assertEqual(admit(channel, operation(m="e", d=0), snapshot, IDENTITY).command, STOP)

    def test_stop_permitted_on_paused_share_but_never_unknown_code(self):
        snapshot = snapshot_from_registration(registration(p={"a": share(p=True, z=False)}), IDENTITY)
        self.assertEqual(admit(self.shared, operation(m="e", d=0), snapshot, IDENTITY).command, STOP)
        self.assertIsNone(admit(b"c4100-sops-unknown", operation(m="e", d=0), snapshot, IDENTITY))
        self.assertIsNone(admit(self.owner, operation(m="e", d=100), snapshot, IDENTITY))

    def test_exact_owned_channel_and_target(self):
        for channel in (b"c4101-ops", b"4100-ops", b"c4100-op", self.owner+b"-extra",
                        self.shared+b"extra", b"c4100-sops-", b"c4100-ping", b"x"*257):
            self.assertIsNone(admit(channel, operation(), self.snapshot, IDENTITY))
        self.assertIsNone(admit(self.owner, operation(id=1235), self.snapshot, IDENTITY))
        other = copy.copy(IDENTITY)
        other.shocker_id = 1235
        self.assertIsNone(admit(self.owner, operation(id=1235), self.snapshot, other))

    def test_numeric_and_required_field_validation(self):
        for patch in ({"id": True}, {"i": True}, {"d": False}, {"r": 1},
                      {"i": -1}, {"i": 101}, {"d": 99}, {"d": 10001},
                      {"d": 100.0}, {"m": "shock"}, {"l": "secret"}, {"extra": 1}):
            with self.subTest(patch=patch):
                self.assertIsNone(admit(self.owner, operation(**patch), self.snapshot, IDENTITY))
        for name in ("id", "m", "i", "d", "r"):
            value = json.loads(operation())
            del value[name]
            self.assertIsNone(admit(self.owner, json.dumps(value).encode(), self.snapshot, IDENTITY))

    def test_metadata_cannot_grant_permissions_and_is_not_retained(self):
        metadata = {"u": 1, "ty": "ow", "o": "PRIVATE_ORIGIN", "h": True}
        decision = admit(self.shared, operation(i=100, l=metadata), self.snapshot, IDENTITY)
        self.assertEqual(decision.command.intensity, 15)
        self.assertNotIn("PRIVATE_ORIGIN", repr(decision))
        self.assertIsNone(admit(b"c4100-sops-unknown", operation(l=metadata), self.snapshot, IDENTITY))

    def test_bad_json_duplicates_size_depth_and_nonbytes(self):
        bad = [b"{}", b"[1]", b"\xff", b'{"id":1234,"id":1234}',
               b'{"l":{"u":1,"u":2}}', b" "*(MAX_COMMAND_BYTES+1),
               b"["*1000, b'{"id":NaN}', "not bytes"]
        for payload in bad:
            self.assertIsNone(admit(self.owner, payload, self.snapshot, IDENTITY))


if __name__ == "__main__":
    unittest.main()
