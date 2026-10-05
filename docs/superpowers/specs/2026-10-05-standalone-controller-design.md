# Standalone PiShock Controller design

Date: 2026-10-05

Status: Written design and implementation plan approved on 2026-10-05. Implemented locally with automatic selected-target assignment, software verification, and independent review. Physical controller installation, input/display, and receiver-delivery validation remain pending.

## Purpose

Provide a separate Flipper Zero application for operating the user's selected SmallOne shocker without a computer, internet connection, or original PiShock hub after setup. Support beep, vibration, and shock with local intensity and duration controls. Keep the existing PiShock USB Radio application and desktop bridge usable.

The application is an add-on installed on the SD card. It does not flash firmware, format storage, or alter the Flipper's normal operation. Public source and binaries contain no personal device identity.

## User experience

The new application is named **PiShock Controller** and appears in the Flipper's Sub-GHz apps. It starts disarmed, with Beep selected, intensity zero, and a 0.5-second duration. The screen shows the selected target, armed/disarmed state, selected mode, intensity, and duration.

- Up/Down selects Mode, Intensity, or Duration. Left/Right changes the selected value.
- Beep always uses zero intensity. Vibration and shock support 0–100%; the existing protocol encoder maps the top value to its supported maximum.
- Duration ranges from 0.1 to 10.0 seconds in 0.1-second steps. Button repeat may adjust settings, but never starts an operation.
- Hold OK to arm. Release it, then make a fresh OK press to send one timed operation. Holding the arming button must never immediately operate the shocker.
- Arming remains active after a completed operation. Changing any setting disarms. Settings cannot change during transmission.
- Back press immediately stops transmission and disarms. Holding Back exits after stopping. Stop/exit actions remain available from any state.
- Only physical button input can arm or start an operation. Repeated, queued, or software-injected input must not retrigger output. There is no operation queue or automatic repeat.
- The screen reports local transmission and completion. It never claims that the receiver acknowledged a packet.

There is no automatic keep-awake in this first controller release. PiShock website settings, pause controls, sharing permissions, and cloud commands do not control this offline application. The Windows bridge retains its existing validated cloud behavior.

## Setup and privacy

The desktop **Set up devices** page gains an **Install standalone controller** action that installs the matching application and automatically writes the selected shocker's ID and channel. A separate **Update controller target** action changes the target without reinstalling the app. These use the already selected saved profile; the user does not enter identifiers at a terminal. Both actions require the bridge/direct test to be stopped and Flipper applications to be closed. Installation reports completion only after both application and target have been read back and verified; a partial result explains which step must be repeated.

The controller app is installed as `/ext/apps/Sub-GHz/pishock_controller.fap`. Its target is stored in `/ext/apps_data/pishock_controller/target.conf`, using a versioned FlipperFormat file with only:

- Radio/shocker ID: integer 1–65535.
- Radio channel: integer 0–2.

The file contains no hub MAC address, hub ID, account/owner ID, IP address, Wi-Fi settings, API keys, or complete Windows profile. It is readable SD-card configuration, not encrypted storage. Real target files never enter source control or release packages.

Missing, malformed, unsupported-version, or out-of-range configuration leaves the app unable to arm and displays a setup instruction. Target configuration is read at launch. Armed/running state, last mode, intensity, and duration are not persisted. The selected target remains visible so the user can check it before arming.

Installation detects the existing firmware API and copies the matching FAP, with staged upload, readback verification, and backup/restore behavior matching the existing installer. Target updates use the same bounded, verified-file approach while the controller is closed. Firmware storage rename is not assumed atomic. Any interrupted final copy is reported as an incomplete update; the app rejects invalid configuration. Neither action automatically launches the controller or transmits.

The initial release uses the two SDK/API targets already supported by this project: official firmware 1.4.3/API 87.1 and API 88.9, hardware f7. Compatibility checks stay at the application-install level and do not trigger firmware changes.

## Implementation boundaries

`controller/controller_core.c` and `.h` provide a portable input/state model: disarmed, armed idle, operating, terminating, and exiting. Tests exercise button sequences, parameter bounds, one-shot operation, and stop priority without radio hardware.

`controller/pishock_controller.c` adapts that model to the Flipper GUI, physical input events, storage, monotonic ticks, and Sub-GHz HAL. It owns one finite transmit sequence at a time. The controller has a distinct app ID and entry point; it does not take over USB CDC or depend on the desktop heartbeat.

Reuse `app/radio_core.c` and `.h` as the canonical CaiXianlin encoder. A build staging step copies these public sources into the controller build directory. Do not fork or modify the USB Radio encoder merely to support the new UI.

Transmission uses the existing frequency/preset, finite pulse iterator, deadline handling, and short zero-intensity terminator. Halt DMA before replacing its buffer. On Back, deadline, exit, or error, stop local output and clean up radio/power resources. A start failure disarms and displays an error. No automatic retry follows an uncertain result.

Desktop integration adds the controller assets, installation/copy controls, and clear setup help. It preserves the current bridge and its new direct beep diagnostic. The direct beep feature is a separate, bounded desktop update and can ship before the controller.

## Verification and delivery

- Portable C tests cover fresh-press arming/run sequences, software input rejection, button repeat, settings changes, stop priority, duration expiry, tick wrap, invalid target configuration, and parameter boundaries.
- Existing RF encoder tests remain green. Both supported SDK builds must succeed and their FAP metadata/imports must match the target API.
- Desktop tests cover target-only serialization, API selection, staged/readback failure handling, cancellation, and mutual exclusion with active bridge/direct-test sessions.
- Run the full Python suite, GUI cleanup checks, packaged startup tests, and source/bundle privacy audits.
- Keep diagnostic and controller RF tests explicit: no shock or vibration is sent automatically during development. Physical installation and an off-body beep check can verify device behavior after the builds pass.
- Document installation, controls, pairing, offline behavior, and changes. Preserve Droski1's acknowledgment and upstream protocol/license notices.
- Build Windows installer/portable downloads and both controller FAPs locally. Preserve existing 0.3.1 artifacts and the `cli-original` branch. Provide publishing commands; do not upload automatically.

## Acceptance criteria

After one-time graphical setup, the user can disconnect USB, launch PiShock Controller, select any supported mode/intensity/duration, physically arm, and send one timed operation. Back stops/disarms, repeated button events cannot retrigger, and relaunch always starts disarmed. The existing USB bridge still functions independently. No personal information is embedded in shared source or builds.
