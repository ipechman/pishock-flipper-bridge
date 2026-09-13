"""Start the existing mirror after unambiguous USB and registered-target lookup.

Only the documented hub info request is sent before handing over to the passive
mirror. No packages are installed, and neither device's firmware is changed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
from typing import Any, Iterable

from radio import RadioError, open_serial, read_hub_info, validate_target
import serial_mirror


FLIPPER_USB = (0x0483, 0x5740)
HUB_USB = (0x1A86, 0x7523)


class SetupError(Exception):
    """A concise setup error containing no raw USB metadata or hub data."""


@dataclass(frozen=True)
class BridgePorts:
    hub: str
    flipper: str


def is_radio_interface(port: Any) -> bool:
    """Recognize CDC interface 2 without guessing from the COM number.

    PySerial on Windows reports the composite interface as LOCATION=4-2:x.2.
    A hardware MI_02 marker or explicit interface number is equivalent evidence.
    Physical USB location suffixes without a configuration separator do not count.
    """
    location = getattr(port, "location", None)
    hwid = getattr(port, "hwid", None)
    interface = getattr(port, "interface", None)
    if isinstance(location, str) and re.search(r":(?:x|[0-9]+)\.2$", location, re.IGNORECASE):
        return True
    if isinstance(hwid, str) and (
        re.search(r"(?:^|[&\\\s])MI_02(?:$|[&\\\s])", hwid, re.IGNORECASE)
        or re.search(r"\bLOCATION=[^\s]*:(?:x|[0-9]+)\.2(?:\s|$)", hwid, re.IGNORECASE)
    ):
        return True
    if type(interface) is int:
        return interface == 2
    return isinstance(interface, str) and bool(re.fullmatch(
        r"(?:0?2|(?:USB\s+)?Interface\s+0?2)", interface, re.IGNORECASE))


def checked_port_name(port: Any) -> str:
    device = getattr(port, "device", None)
    if not isinstance(device, str) or not re.fullmatch(r"COM[1-9][0-9]{0,5}", device, re.IGNORECASE):
        raise SetupError("The selected USB device did not report a usable Windows COM port.")
    return device.upper()


def select_ports(ports: Iterable[Any]) -> BridgePorts:
    hubs, flippers = [], []
    for port in ports:
        usb_id = (getattr(port, "vid", None), getattr(port, "pid", None))
        if usb_id == HUB_USB:
            hubs.append(port)
        elif usb_id == FLIPPER_USB and is_radio_interface(port):
            flippers.append(port)
    if not hubs:
        raise SetupError("Connect the original PiShock hub by USB, then reopen Start bridge.")
    if len(hubs) != 1:
        raise SetupError("More than one possible PiShock hub is connected. Leave one connected, then reopen Start bridge.")
    if not flippers:
        raise SetupError("Open PiShock USB Radio on the connected Flipper, then reopen Start bridge.")
    if len(flippers) != 1:
        raise SetupError("More than one Flipper radio interface is connected. Leave one connected, then reopen Start bridge.")
    selected = BridgePorts(checked_port_name(hubs[0]), checked_port_name(flippers[0]))
    if selected.hub == selected.flipper:
        raise SetupError("The hub and Flipper resolved to the same USB port; automatic setup stopped.")
    return selected


def select_shocker(info: Any) -> int:
    """Choose exactly one registered SmallOne; never infer or change pairing."""
    if not isinstance(info, dict) or not isinstance(info.get("shockers"), list):
        raise SetupError("The hub did not return a registered shocker list. Check the PiShock website.")
    candidates = []
    for shocker in info["shockers"]:
        if not isinstance(shocker, dict) or type(shocker.get("type")) is not int or shocker["type"] != 1:
            continue
        target = shocker.get("id")
        if type(target) is not int or not 1 <= target <= 65535:
            raise SetupError("A registered SmallOne has an unsupported ID; automatic setup stopped.")
        candidates.append(target)
    if not candidates:
        raise SetupError("No registered SmallOne shocker was found on this hub. Check pairing on the PiShock website.")
    if len(candidates) != 1:
        raise SetupError("This hub has more than one SmallOne shocker. Use the explicit mirror setup to choose a target.")
    return candidates[0]


def find_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        raise SetupError(
            "Python is available, but USB support (pyserial) is missing. "
            "Install the host/requirements.txt dependencies once, then reopen Start bridge. "
            "No packages were installed automatically.") from None
    return list_ports.comports()


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Find one PiShock hub and one Flipper radio app, then start USB forwarding.")
    argument_parser.add_argument("--beep-only", action="store_true", help="Allow only beep and stop for commissioning")
    argument_parser.add_argument("--channel", type=int, default=0, help="Radio channel 0, 1 or 2; default 0")
    return argument_parser


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_target(1, args.channel)
        ports = select_ports(find_ports())
        print("Reading the original PiShock hub's registered shocker...", flush=True)
        with open_serial(ports.hub, original_hub=True) as hub:
            # read_hub_info returns a whitelist; Wi-Fi passwords and OTK are
            # discarded by that helper and never printed, saved, or forwarded.
            shocker_id = select_shocker(read_hub_info(hub))
        print(f"PiShock {ports.hub}; Flipper {ports.flipper}; shocker {shocker_id}.", flush=True)
        print("Keep this window open. Press Ctrl+C here to stop and disarm the bridge.", flush=True)
        arguments = ["--hub-port", ports.hub, "--port", ports.flipper,
                     "--id", str(shocker_id), "--channel", str(args.channel)]
        if args.beep_only:
            arguments.append("--beep-only")
        return serial_mirror.main(arguments)
    except KeyboardInterrupt:
        print("Setup canceled.", file=sys.stderr)
        return 130
    except (SetupError, RadioError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("USB setup failed. Close other programs using these devices, check both USB cables, then reopen Start bridge.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
