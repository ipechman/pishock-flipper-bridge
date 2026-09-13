"""Offline selection and launcher handoff checks; no USB devices accessed."""

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import start_bridge as start


def port(device, vid, pid, location=None, interface=None, hwid=""):
    return SimpleNamespace(device=device, vid=vid, pid=pid, location=location,
                           interface=interface, hwid=hwid)


def observed_ports():
    return [port("COM5", *start.FLIPPER_USB, location="4-2:x.0"),
            port("COM7", *start.FLIPPER_USB, location="4-2:x.2"),
            port("COM6", *start.HUB_USB, location="4-3:x.0")]


class SelectionTests(unittest.TestCase):
    def test_observed_windows_interfaces_select_extra_cdc(self):
        self.assertEqual(start.select_ports(observed_ports()), start.BridgePorts("COM6", "COM7"))

    def test_interface_equivalents_do_not_guess_from_com_number(self):
        for metadata in ({"location": "2-4:1.2"}, {"hwid": r"USB\VID_0483&PID_5740&MI_02\DEVICE"},
                         {"hwid": "USB VID:PID=0483:5740 LOCATION=4-2:x.2"},
                         {"interface": 2}, {"interface": "Interface 2"}):
            with self.subTest(metadata=metadata):
                self.assertTrue(start.is_radio_interface(port("COM79", *start.FLIPPER_USB, **metadata)))
        for metadata in ({}, {"location": "1-8.2"}, {"location": "4-2:x.0"},
                         {"interface": "USB Serial Device (COM2)"}, {"interface": True}):
            with self.subTest(metadata=metadata):
                self.assertFalse(start.is_radio_interface(port("COM7", *start.FLIPPER_USB, **metadata)))

    def test_missing_or_ambiguous_device_never_chooses_arbitrarily(self):
        valid = observed_ports()
        for ports in ([], valid[:2], valid[::2], valid + [port("COM7", *start.HUB_USB)],
                      valid + [port("COM10", *start.FLIPPER_USB, location="1-7:x.2")]):
            with self.subTest(ports=ports), self.assertRaises(start.SetupError):
                start.select_ports(ports)

    def test_unrelated_usb_serial_device_is_not_a_flipper(self):
        with self.assertRaises(start.SetupError):
            start.select_ports([port("COM6", *start.HUB_USB), port("COM7", 0x1234, 0x5740, location="4-2:x.2")])

    def test_untrusted_port_name_is_not_echoed(self):
        items = observed_ports()
        items[1].device = "secret command"
        with self.assertRaises(start.SetupError) as error:
            start.select_ports(items)
        self.assertNotIn("secret", str(error.exception))

    def test_one_smallone_is_selected_and_pausing_is_preserved(self):
        info = {"shockers": [{"id": 7, "type": 0}, {"id": 1234, "type": 1, "paused": True}]}
        self.assertEqual(start.select_shocker(info), 1234)
        self.assertTrue(info["shockers"][1]["paused"])

    def test_missing_ambiguous_and_invalid_targets_fail_without_raw_data(self):
        for info in ({}, {"shockers": []}, {"shockers": [{"id": 7, "type": 0}]},
                     {"shockers": [{"id": 1, "type": 1}, {"id": 2, "type": 1}]},
                     {"shockers": [{"id": True, "type": 1}]},
                     {"shockers": [{"id": 65536, "type": 1}]},
                     {"shockers": [{"id": "secret", "type": 1}]},
                     {"shockers": [{"id": 1, "type": True}]}):
            with self.subTest(info=info):
                with self.assertRaises(start.SetupError) as error:
                    start.select_shocker(info)
                self.assertNotIn("secret", str(error.exception))


class HandoffTests(unittest.TestCase):
    def test_handoff_reuses_explicit_mirror_cli_and_only_optional_beep_mode(self):
        for arguments in ([], ["--beep-only"]):
            with self.subTest(arguments=arguments), patch.object(start, "find_ports", return_value=observed_ports()), \
                 patch.object(start, "open_serial") as opening, \
                 patch.object(start, "read_hub_info", return_value={"shockers": [{"id": 1234, "type": 1}]}), \
                 patch.object(start.serial_mirror, "main", return_value=0) as handoff, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(start.main(arguments), 0)
                opening.assert_called_once_with("COM6", original_hub=True)
                expected = ["--hub-port", "COM6", "--port", "COM7", "--id", "1234", "--channel", "0"]
                handoff.assert_called_once_with(expected + arguments)
                opening.return_value.__exit__.assert_called_once()

    def test_ambiguous_port_does_not_open_any_device(self):
        with patch.object(start, "find_ports", return_value=[]), patch.object(start, "open_serial") as opening, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(start.main([]), 1)
        opening.assert_not_called()

    def test_ambiguous_shocker_does_not_start_mirror(self):
        output = io.StringIO()
        info = {"shockers": [{"id": 1, "type": 1}, {"id": 2, "type": 1}], "password": "secret"}
        with patch.object(start, "find_ports", return_value=observed_ports()), patch.object(start, "open_serial"), \
             patch.object(start, "read_hub_info", return_value=info), patch.object(start.serial_mirror, "main") as handoff, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(start.main([]), 1)
        handoff.assert_not_called()
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
