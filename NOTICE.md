# Source and license notice

This project is distributed under GNU GPL version 3; source-file license notices apply. See [LICENSE](LICENSE). Source for the included Flipper application is in `app/`; the desktop application and bridge are in `host/`. Build instructions are in [docs/BUILD.md](docs/BUILD.md).

## Inspiration and upstream work

**Droski1's [PiShock-Unofficial-Documentation](https://github.com/Droski1/PiShock-Unofficial-Documentation) inspired this project.** Its community documentation was the starting point for exploring PiShock communications.

The CaiXianlin encoder is adapted from [OpenShock/FlipperZero](https://github.com/OpenShock/FlipperZero), `protocols.c`, commit `0457742b12f8853f1b09c73ad7e1b9dc4f8cc8c8`.

Stop behavior was checked against [OpenShock/Firmware](https://github.com/OpenShock/Firmware), commit `025b4436f8e1dd7abc20e83e7c9d1cca50bfd1fd`, `src/radio/RFTransmitter.cpp` and `src/radio/rmt/Sequence.cpp`.

The 0.3 keep-awake behavior follows that same revision's [CommandHandler.cpp](https://github.com/OpenShock/Firmware/blob/025b4436f8e1dd7abc20e83e7c9d1cca50bfd1fd/src/CommandHandler.cpp): an inactivity interval of 60 seconds and a short zero-intensity vibration transmission. The [CaiXianlin packet format](https://wiki.openshock.org/hardware/shockers/caixianlin) has no duration field. The session gate, idle scheduling, command priority, and USB integration were implemented for this project.

The finite transmission iterator, parser, USB application, desktop interface, and host tools were written for this project. No affiliation or endorsement by PiShock, Flipper Devices, Droski1, or OpenShock is implied.

## Flipper application and installation

The official FAP targets Flipper Devices' **official SDK 1.4.3, API 87.1, hardware f7**, with toolchain 39. Its 54 imported symbols are enabled exports in that official SDK. A second build of the same application source targets **API 88.9, hardware f7**, using the [DarkFlippers SDK snapshot](https://github.com/DarkFlippers/unleashed-firmware/releases/tag/unlshd-093). The installer selects the matching application for the device API. Custom firmware is not required; installing either FAP installs only an add-on application.

The desktop installer uses Flipper Devices' existing `device_info`, `loader info`, and USB storage commands. Protocol behavior was checked against the official [storage helper](https://github.com/flipperdevices/flipperzero-firmware/blob/1.4.3/scripts/flipper/storage.py), [storage CLI](https://github.com/flipperdevices/flipperzero-firmware/blob/1.4.3/applications/services/storage/storage_cli.c), [storage implementation](https://github.com/flipperdevices/flipperzero-firmware/blob/1.4.3/applications/services/storage/storage_external_api.c), and [loader CLI](https://github.com/flipperdevices/flipperzero-firmware/blob/1.4.3/applications/services/loader/loader_cli.c).

USB uploads and readbacks are implemented independently with bounded length framing. Final storage replacement uses the firmware's copy-and-delete rename behavior and is not guaranteed to be atomic during power loss. The installer never flashes firmware, formats storage, changes radio settings, or starts an application.

## Device protocol and distributed files

The host implementation was checked against PiShock Next firmware 3.1.4.251129.2525, obtained from the public [vendor firmware endpoint](https://do.pishock.com/api/GetLatestFirmware?type=3) for static inspection. Vendor firmware is not redistributed.

The standalone connection implements the owned-device protocol independently. The public package contains no imported device identity or live-session logs. Device IDs, MAC addresses, and IP addresses used in tests and the explicit UI demo are synthetic examples.

The Windows desktop distribution bundles its required Python runtime and libraries. The packaging process includes third-party license notices alongside the application; see the release build instructions for dependency versions.
