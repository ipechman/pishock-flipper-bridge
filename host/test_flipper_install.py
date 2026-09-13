"""Installer protocol tests: no serial devices are opened."""

from pathlib import Path
import hashlib
import shlex
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import flipper_install as install


def make_fap(api=(87, 1), target=7):
    header = bytearray(52)
    header[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<H", header, 18, 40)
    struct.pack_into("<I", header, 32, 52)
    struct.pack_into("<HHH", header, 46, 40, 3, 1)
    names = b"\0.shstrtab\0.fapmeta\0"
    sections = bytearray(120)
    struct.pack_into("<I", sections, 40, 1)
    struct.pack_into("<II", sections, 56, 172, len(names))
    struct.pack_into("<I", sections, 80, 11)
    struct.pack_into("<II", sections, 96, 172 + len(names), 14)
    manifest = struct.pack("<IIIh", 0x52474448, 1, (api[0] << 16) | api[1], target)
    # Include every byte value, CLI prompts/commands, and line delimiters in
    # payload; delimiter-based binary reading or accidental echo corrupts it.
    payload = (bytes(range(256)) + b">: \r\nReady?\r\n\x03storage remove /ext\r") * 9
    return bytes(header + sections) + names + manifest + payload


class FakeFlipper:
    def __init__(self, *, api=(87, 1), target=7, files=None, partial_write=23, partial_read=17):
        self.api = api
        self.target = target
        self.files = dict(files or {})
        self.input = bytearray()
        self.output = bytearray(b"Welcome\r\n>: ")
        self.commands = []
        self.closed = False
        self.partial_write = partial_write
        self.partial_read = partial_read
        self.binary = None
        self.read_pending = None
        self.write_count = 0
        self.fail_chunk = None
        self.break_binary = False
        self.break_rename = False
        self.corrupt_staging = False
        self.corrupt_final = False
        self.zero_write = False
        self.app_running = False

    def factory(self, **kwargs):
        self.settings = kwargs
        return self

    def close(self):
        self.closed = True

    def read(self, count):
        count = min(count, self.partial_read, len(self.output))
        data = bytes(self.output[:count])
        del self.output[:count]
        return data

    def write(self, data):
        if self.zero_write:
            return 0
        if self.binary is not None and self.break_binary:
            raise OSError("private USB device serial should never escape")
        count = min(len(data), self.partial_write)
        for byte in data[:count]:
            if self.binary is not None:
                path, need, payload = self.binary
                payload.append(byte)
                if len(payload) == need:
                    self.files[path] = self.files.get(path, b"") + bytes(payload)
                    self.write_count += 1
                    self.binary = None
                    if self.write_count == self.fail_chunk:
                        self.output.extend(b"Storage error: filesystem not ready\r\n")
                    self.output.extend(b"\r\n>: ")
            elif self.read_pending is not None:
                if byte != ord("y"):
                    raise AssertionError("binary readback handshake missing")
                payload, position, chunk = self.read_pending
                end = min(len(payload), position + chunk)
                self.output.extend(payload[position:end])
                if end < len(payload):
                    self.read_pending = payload, end, chunk
                    self.output.extend(b"\r\nReady?\r\n")
                else:
                    self.read_pending = None
                    self.output.extend(b"\r\n>: ")
            elif byte == 13:
                command = self.input.decode("ascii")
                self.input.clear()
                self._command(command)
            else:
                self.input.append(byte)
        return count

    def _command(self, command):
        self.output.extend(command.encode("ascii") + b"\r\n")
        if not command:
            self.output.extend(b">: ")
            return
        self.commands.append(command)
        if command == "device_info":
            self.output.extend((f"hardware_model: Flipper Zero\r\nhardware_uid: PRIVATE-ID\r\n"
                                f"hardware_target: {self.target}\r\nfirmware_version: 1.4.3\r\n"
                                f"firmware_api_major: {self.api[0]}\r\n"
                                f"firmware_api_minor: {self.api[1]}\r\n").encode())
        elif command == "loader info":
            self.output.extend(b'Application "Private app name" is running\r\n' if self.app_running
                               else b"No application is running\r\n")
        else:
            parts = shlex.split(command)
            if parts[:2] == ["storage", "mkdir"]:
                pass
            elif parts[:2] == ["storage", "stat"]:
                if parts[2] in self.files:
                    self.output.extend(f"File, size: {len(self.files[parts[2]])}b\r\n".encode())
                else:
                    self.output.extend(b"Storage error: file/dir not exist\r\n")
            elif parts[:2] == ["storage", "write_chunk"]:
                self.binary = parts[2], int(parts[3]), bytearray()
                self.output.extend(b"Ready\r\n")
                return
            elif parts[:2] == ["storage", "read_chunks"]:
                payload = self.files[parts[2]]
                if (self.corrupt_staging and parts[2].endswith(".upload")) or (
                        self.corrupt_final and parts[2] == install.APP_PATH):
                    payload = payload[:-1] + bytes([payload[-1] ^ 1])
                self.output.extend(f"Size: {len(payload)}\r\n".encode())
                if payload:
                    self.read_pending = payload, 0, int(parts[3])
                    self.output.extend(b"\r\nReady?\r\n")
                    return
            elif parts[:2] == ["storage", "remove"]:
                self.files.pop(parts[2], None)
            elif parts[:2] in (["storage", "copy"], ["storage", "rename"]):
                if parts[1] == "copy" and parts[3] in self.files:
                    self.output.extend(b"Storage error: file/dir already exist\r\n\r\n>: ")
                    return
                self.files[parts[3]] = self.files[parts[2]]
                if parts[1] == "rename":
                    if self.break_rename:
                        self.files[parts[3]] = b"partial copy"
                        self.output.extend(b"Storage error: internal error\r\n")
                    else:
                        del self.files[parts[2]]
            else:
                raise AssertionError(f"Unexpected command: {command}")
        self.output.extend(b"\r\n>: ")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.asset_dir = Path(self.directory.name)
        self.payload = make_fap()
        for api, name in install.ASSETS.items():
            (self.asset_dir / name).write_bytes(make_fap(api))

    def run_install(self, fake, **kwargs):
        return install.install_app("COM5", self.asset_dir, serial_factory=fake.factory, **kwargs)

    def test_installs_arbitrary_binary_with_partial_reads_and_writes(self):
        fake = FakeFlipper()
        result = self.run_install(fake)
        self.assertEqual(fake.files, {install.APP_PATH: self.payload})
        self.assertEqual(result.path, install.APP_PATH)
        self.assertEqual(result.device.api_major, 87)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.settings["write_timeout"], install.IO_TIMEOUT)
        self.assertTrue(all(command in ("device_info", "loader info") or command.startswith("storage ") for command in fake.commands))
        self.assertNotIn("PRIVATE-ID", repr(result))
        self.assertFalse(any("loader open" in command or "loader close" in command or "power" in command or "format" in command for command in fake.commands))

    def test_read_only_inspection_does_not_touch_storage(self):
        fake = FakeFlipper(api=(88, 9))
        info = install.inspect_device("COM5", serial_factory=fake.factory)
        self.assertTrue(info.supported)
        self.assertEqual(fake.commands, ["device_info"])
        self.assertTrue(fake.closed)

    def test_wrong_api_prevents_all_storage_writes(self):
        for api in ((88, 8), (88, 10), (87, 0), (87, 2), (86, 1), (89, 1)):
            with self.subTest(api=api):
                fake = FakeFlipper(api=api)
                with self.assertRaisesRegex(install.InstallError, "matching your firmware"):
                    self.run_install(fake)
                self.assertEqual(fake.commands, ["device_info"])

    def test_matching_api_build_selected_without_changing_firmware(self):
        for api in ((87, 1), (88, 9)):
            with self.subTest(api=api):
                fake = FakeFlipper(api=api, files={install.APP_PATH: b"previous app"})
                result = self.run_install(fake)
                expected = make_fap(api)
                self.assertEqual(fake.files, {install.APP_PATH: expected})
                self.assertEqual(result.sha256, hashlib.sha256(expected).hexdigest())
                self.assertEqual((result.device.api_major, result.device.api_minor), api)
                self.assertTrue(fake.closed)
                self.assertTrue(all(command in ("device_info", "loader info")
                                    or command.startswith("storage ") for command in fake.commands))

    def test_matching_api_with_wrong_hardware_prevents_storage_writes(self):
        for api in install.ASSETS:
            with self.subTest(api=api):
                fake = FakeFlipper(api=api, target=8)
                with self.assertRaisesRegex(install.InstallError, "hardware f8"):
                    self.run_install(fake)
                self.assertEqual(fake.commands, ["device_info"])
                self.assertTrue(fake.closed)

    def test_missing_selected_asset_does_not_fall_back_to_other_api(self):
        (self.asset_dir / install.ASSETS[(88, 9)]).unlink()
        fake = FakeFlipper(api=(88, 9))
        with self.assertRaisesRegex(install.InstallError, "bundled Flipper application"):
            self.run_install(fake)
        self.assertEqual(fake.commands, ["device_info"])
        self.assertTrue(fake.closed)

    def test_missing_unused_asset_does_not_block_matching_install(self):
        (self.asset_dir / install.ASSETS[(87, 1)]).unlink()
        fake = FakeFlipper(api=(88, 9))
        self.run_install(fake)
        self.assertEqual(fake.files, {install.APP_PATH: make_fap((88, 9))})

    def test_running_app_prevents_storage_writes_without_exposing_name(self):
        fake = FakeFlipper()
        fake.app_running = True
        with self.assertRaisesRegex(install.InstallError, "Close the app") as caught:
            self.run_install(fake)
        self.assertNotIn("Private", str(caught.exception))
        self.assertEqual(fake.commands, ["device_info", "loader info"])

    def test_misnamed_asset_cannot_install_different_api(self):
        for requested, wrong in (((87, 1), (88, 9)), ((88, 9), (87, 1))):
            with self.subTest(api=requested):
                (self.asset_dir / install.ASSETS[requested]).write_bytes(make_fap(wrong))
                fake = FakeFlipper(api=requested)
                with self.assertRaisesRegex(install.InstallError, "bundled Flipper application"):
                    self.run_install(fake)
                self.assertEqual(fake.commands, ["device_info"])
                self.assertTrue(fake.closed)

    def test_asset_with_wrong_hardware_rejected_before_storage_writes(self):
        for api, name in install.ASSETS.items():
            with self.subTest(api=api):
                (self.asset_dir / name).write_bytes(make_fap(api, target=8))
                fake = FakeFlipper(api=api)
                with self.assertRaisesRegex(install.InstallError, "bundled Flipper application"):
                    self.run_install(fake)
                self.assertEqual(fake.commands, ["device_info"])

    def test_actual_bundled_assets_match_api_and_published_checksums(self):
        root = Path(__file__).resolve().parents[1]
        checksums = {line.split()[1]: line.split()[0]
                     for line in (root / "SHA256SUMS").read_text().splitlines() if line.strip()}
        for api, name in install.ASSETS.items():
            with self.subTest(api=api):
                data = install._asset_bytes(root, api)
                self.assertGreater(len(data), 1024)
                self.assertEqual(hashlib.sha256(data).hexdigest(), checksums[name])

    def test_unknown_version_label_can_use_matching_api_without_exposing_custom_name(self):
        info = install.parse_device_info(
            b"hardware_model: Flipper Zero\r\nhardware_target: 7\r\n"
            b"firmware_version: Private nickname\r\nfirmware_api_major: 88\r\nfirmware_api_minor: 9\r\n")
        self.assertTrue(info.supported)
        self.assertEqual(info.firmware_version, "Unknown")
        self.assertNotIn("Private", repr(info))

    def test_cancel_before_opening(self):
        cancel = threading.Event()
        cancel.set()
        fake = FakeFlipper()
        with self.assertRaises(install.InstallCancelled):
            self.run_install(fake, cancel=cancel)
        self.assertEqual(fake.commands, [])

    def test_cancel_between_chunks_keeps_original_and_cleans_staging(self):
        original = b"original app"
        fake = FakeFlipper(files={install.APP_PATH: original, "/ext/other.txt": b"keep"})
        cancel = threading.Event()

        def progress(percent, _message):
            if 5 < percent < 70:
                cancel.set()

        with self.assertRaises(install.InstallCancelled):
            self.run_install(fake, cancel=cancel, progress=progress)
        self.assertEqual(fake.files, {install.APP_PATH: original, "/ext/other.txt": b"keep"})
        self.assertEqual(fake.write_count, 1)
        self.assertFalse(any("rename" in command for command in fake.commands))

    def test_cancel_at_verification_keeps_original(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old"})
        cancel = threading.Event()

        def progress(percent, _message):
            if percent == 70:
                cancel.set()

        with self.assertRaises(install.InstallCancelled):
            self.run_install(fake, cancel=cancel, progress=progress)
        self.assertEqual(fake.files, {install.APP_PATH: b"old"})

    def test_verified_replacement_keeps_other_files(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old", "/ext/unrelated.fap": b"other"})
        self.run_install(fake)
        self.assertEqual(fake.files, {install.APP_PATH: self.payload, "/ext/unrelated.fap": b"other"})
        commands = "\n".join(fake.commands)
        self.assertLess(commands.index(".backup\""), commands.index("storage rename"))

    def test_identical_install_is_only_verified(self):
        fake = FakeFlipper(files={install.APP_PATH: self.payload})
        self.run_install(fake)
        self.assertEqual(fake.write_count, 0)
        self.assertFalse(any("rename" in command for command in fake.commands))

    def test_failed_chunk_cleans_only_its_temporary_file(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old", "/ext/other": b"keep"})
        fake.fail_chunk = 2
        with self.assertRaises(install.InstallError):
            self.run_install(fake)
        self.assertEqual(fake.files, {install.APP_PATH: b"old", "/ext/other": b"keep"})

    def test_broken_binary_stream_never_sends_cleanup_as_payload(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old"})
        fake.break_binary = True
        with self.assertRaisesRegex(install.InstallError, "disconnected") as caught:
            self.run_install(fake)
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(fake.commands[-1].startswith("storage write_chunk"))
        self.assertEqual(fake.files[install.APP_PATH], b"old")
        self.assertTrue(fake.closed)

    def test_corrupt_staging_does_not_replace_original(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old"})
        fake.corrupt_staging = True
        with self.assertRaisesRegex(install.InstallError, "did not pass verification"):
            self.run_install(fake)
        self.assertEqual(fake.files, {install.APP_PATH: b"old"})
        self.assertFalse(any("rename" in command for command in fake.commands))

    def test_failed_final_copy_restores_verified_backup(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old"})
        fake.break_rename = True
        with self.assertRaisesRegex(install.InstallError, "restored and verified"):
            self.run_install(fake)
        self.assertEqual(fake.files[install.APP_PATH], b"old")
        self.assertTrue(any(path.endswith(".backup") for path in fake.files))

    def test_cancel_during_commit_finishes_verified_install(self):
        fake = FakeFlipper(files={install.APP_PATH: b"old"})
        cancel = threading.Event()

        def progress(percent, _message):
            if percent == 85:
                cancel.set()

        self.run_install(fake, cancel=cancel, progress=progress)
        self.assertEqual(fake.files, {install.APP_PATH: self.payload})

    def test_unknown_device_rejected_without_raw_identity(self):
        with self.assertRaises(install.InstallError) as caught:
            install.parse_device_info(b"hardware_uid: hidden-owner-serial\r\n")
        self.assertNotIn("hidden-owner", str(caught.exception))

    def test_ambiguous_device_info_rejected(self):
        with self.assertRaisesRegex(install.InstallError, "ambiguous"):
            install.parse_device_info(b"firmware_api_major: 87\r\nfirmware_api_major: 88\r\n")

    def test_conflicting_hardware_and_firmware_target_rejected(self):
        with self.assertRaisesRegex(install.InstallError, "Could not identify"):
            install.parse_device_info(
                b"hardware_model: Flipper Zero\r\nhardware_target: 8\r\nfirmware_target: 7\r\n"
                b"firmware_api_major: 88\r\nfirmware_api_minor: 9\r\n")

    def test_console_discovery_omits_radio_and_unrelated_usb(self):
        ports = [SimpleNamespace(device="COM5", vid=0x0483, pid=0x5740, location="4-2:x.0"),
                 SimpleNamespace(device="COM7", vid=0x0483, pid=0x5740, location="4-2:x.2"),
                 SimpleNamespace(device="COM6", vid=0x1A86, pid=0x7523)]
        self.assertEqual(install.discover_console_ports(ports), [install.PortChoice("COM5", "Flipper USB — COM5")])

    def test_open_errors_do_not_expose_serial_exception(self):
        def fail(**_kwargs):
            raise OSError("personal serial detail")
        with self.assertRaises(install.InstallError) as caught:
            install.inspect_device("COM5", serial_factory=fail)
        self.assertNotIn("personal", str(caught.exception))

    def test_stalled_write_is_bounded(self):
        fake = FakeFlipper()
        fake.zero_write = True
        with patch.object(install.time, "monotonic", side_effect=[0, 9]):
            with self.assertRaisesRegex(install.InstallError, "stopped responding"):
                install.inspect_device("COM5", serial_factory=fake.factory)
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
