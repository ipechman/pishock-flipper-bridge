# Source and license notice

This project is distributed under GNU GPL version 3. See [LICENSE](LICENSE).
Source for the included compiled applications is in `app/`; build instructions
are in [docs/BUILD.md](docs/BUILD.md).

The CaiXianlin encoder is adapted from
[OpenShock/FlipperZero](https://github.com/OpenShock/FlipperZero), `protocols.c`,
commit `0457742b12f8853f1b09c73ad7e1b9dc4f8cc8c8`.

The finite transmission iterator, parser, USB application, and host tools were
written for this project. No endorsement by PiShock, Flipper Devices, or
OpenShock is implied.

Stop behavior was checked against [OpenShock/Firmware](https://github.com/OpenShock/Firmware),
commit `025b4436f8e1dd7abc20e83e7c9d1cca50bfd1fd`,
`src/radio/RFTransmitter.cpp` and `src/radio/rmt/Sequence.cpp`.

The included builds use Flipper Devices' official SDK 1.4.3 (API 87.1, f7)
and the Unleashed 093 SDK (API 88.9, f7), with toolchain 39.
These are application builds for those existing firmware versions;
installing a FAP does not install firmware.

The host implementation was checked against PiShock Next firmware
3.1.4.251129.2525, obtained from the public
[vendor firmware endpoint](https://do.pishock.com/api/GetLatestFirmware?type=3)
for static inspection. Vendor firmware is not redistributed.

The standalone connection implements the owned-device protocol independently.
The public package contains no imported device identity or live-session logs.
Test device IDs, MAC addresses, and IP addresses are synthetic examples.
