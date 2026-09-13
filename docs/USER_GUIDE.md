# User guide

The computer takes over your existing PiShock hub's backend connection. PiShock's
website sends commands to that hub identity; the computer forwards accepted
commands over USB to the Flipper, which transmits to the selected shocker.

The original hub is needed for the one-time import. During normal operation,
leave the original hub unplugged and keep the computer, internet connection,
Flipper USB cable, and bridge window active.

## What you need

- A Windows computer with Python 3.10 or newer.
- Your own already claimed PiShock Next/Lite hub and a paired SmallOne shocker.
  The importer checks that the selected shocker is registered on that hub and
  uses the supported SmallOne model.
- A Flipper Zero with a compatible app build and a USB data cable.
- This project extracted into a writable folder on the computer.

One selected shocker is supported per saved profile. This implementation uses the
Flipper's internal radio; it does not require an external transmitter module.

All example port names and IDs below are fictional. Replace them with the values
for your own devices. Run commands from the project folder.

## 1. Install the matching Flipper app

Check the firmware version and API compatibility of the firmware already on your
Flipper. Choose the matching application:

| Existing Flipper firmware | API | Application file |
| --- | --- | --- |
| Unleashed 093 | 88.9 | `pishock_usb_radio.fap` |
| Official 1.4.3 | 87.1 | `pishock_usb_radio-official-1.4.3.fap` |

These are application files, not firmware images. For a different firmware/API,
follow [the build instructions](BUILD.md) to build against its matching SDK.
An API mismatch means the app needs a compatible build; changing the Flipper's
firmware is not a setup step for this project.

Using a Flipper-compatible file manager, copy the chosen `.fap` into the Sub-GHz
apps folder on the SD card. Open **Apps → Sub-GHz → PiShock USB Radio**. Install
only the build you intend to use so the menu remains unambiguous.

The app temporarily exposes an additional USB serial interface. Its radio port
is different from the Flipper's normal console port. Closing the app restores
normal USB behavior.

## 2. Prepare Python

Open a terminal in the project folder and create a local environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host/requirements.txt
```

If the Windows `py` launcher is unavailable, use `python -m venv .venv` for the
first command. Confirm that this Python installation is version 3.10 or newer.

The runtime dependency is `pyserial`; the backend connection uses Python's
standard library. The launcher prefers the project's `.venv` and otherwise uses
an available `py` or `python` installation. Creating `.venv` makes dependency
selection predictable without requiring environment activation.

## 3. Import your original hub once

Your hub must already belong to your PiShock account, with the intended shocker
paired and registered. This import reads its identity; it does not claim a new
hub, pair a different shocker, or change account ownership.

1. Keep the shocker off-body for setup.
2. Make sure the original hub is online, then connect it to the computer by USB.
   The import needs a valid public IPv4 address reported by the hub. Close other
   programs that are using its serial port.
3. List the available ports:

   ```powershell
   .\.venv\Scripts\python.exe host/radio.py list
   ```

4. Identify the original hub's port. If needed, read its filtered information:

   ```powershell
   .\.venv\Scripts\python.exe host/radio.py hub-info --port HUB_COM
   ```

   Replace `HUB_COM` with the original hub's actual port, such as `COM6`. This
   sends only the hub's information request. It prints a restricted selection of
   fields, including `clientId` and registered shocker IDs; it does not send an
   operation. The hub ID and shocker ID are different values.

5. Import the selected device. For example, **if** your hub ID were `4100`, your
   shocker ID `1234`, and your hub port `COM6`:

   ```powershell
   .\.venv\Scripts\python.exe host/setup_identity.py --hub-port COM6 --hub-id 4100 --shocker-id 1234
   ```

6. Wait for the successful saved-identity message, then unplug the original hub.

The importer verifies the selected hub, its claimed state, and the supported
shocker before saving `.local/device.dpapi`. It selects radio channel 0. The
profile is encrypted for the current Windows account and is not included in the
public project. Wi-Fi passwords and pairing keys from the information response
are discarded.

Import refuses to overwrite an existing profile. To configure a different hub or
shocker, use a fresh copy of the project and repeat setup. On another Windows
account or computer, plan to import from the original hub again.

The profile retains the public IPv4 address reported during import; the bridge
does not discover a new address automatically. If registration fails after your
network or public IP changes, bring the original hub online on the new network
and import again into a fresh project copy.

## 4. Make the first connection with beep-only mode

1. Leave the original PiShock hub unplugged.
2. Connect the Flipper to the computer by USB and open **PiShock USB Radio**.
3. Start the bridge with shock and vibration forwarding disabled:

   ```powershell
   .\.venv\Scripts\python.exe host/standalone.py --beep-only
   ```

4. Wait for **Direct connection ready**. Check that the displayed hub and shocker
   IDs match your selection. On the Flipper, check the target ID and local limit.
5. Press **OK** on the Flipper to arm it.
6. With the powered shocker off-body, send one short beep from PiShock's website
   using the existing hub's controls.

The terminal reports commands accepted by the Flipper. Hearing the beep confirms
receiver delivery for that test; the terminal acknowledgment alone does not.
Beep-only mode ignores shock and vibration commands. A shock test is unnecessary
to check the connection.

Press **Back** on the Flipper to disarm, then **Ctrl+C** in the terminal to end the
test. Start a new normal session for ordinary use.

## 5. Daily use

1. Keep the original hub unplugged.
2. Connect the Flipper by USB and open **PiShock USB Radio**.
3. Double-click **Start standalone.cmd** and keep the window open.
4. Wait for **Direct connection ready** and verify the target.
5. Set the local limit while disarmed, then press **OK** on the Flipper to arm.
6. Use PiShock's website with the existing hub and shocker.

The equivalent terminal command is:

```powershell
.\.venv\Scripts\python.exe host/standalone.py
```

Do not run two bridge windows or run the original hub at the same time as this
replacement. Only one program should own the Flipper radio serial port.

### Flipper controls

| Control | Result |
| --- | --- |
| **OK**, while disarmed and connected | Arms the app |
| **OK**, while armed | Stops and disarms |
| **Back** | Stops and disarms |
| Hold **Back** | Exits the app and restores normal USB mode |
| **Up / Down**, while disarmed and idle | Changes the local intensity cap in 5% steps |
| **Ctrl+C** in the bridge terminal | Ends the bridge and requests Stop and Disarm |

The local cap starts at **20%** whenever the app starts. It applies to shock and
vibration. Commands above that cap are discarded and current output is stopped;
they are not reduced automatically to the cap. Beep has zero intensity.

PiShock's website Stop cancels current output. Use **Back** when you want the
Flipper disarmed as well. If a USB acknowledgment is unavailable, use **Back** on
the device directly.

## Pause, shares, and reconnection

Before admitting commands, the bridge verifies that PiShock's returned hub,
owner, and selected shocker match the saved profile. It checks the shocker's
pause state and accepts shared operations only for an exact known share code.

For shares, it enforces pause state, allowed modes, maximum duration, and maximum
intensity. A duration limit can shorten a request but never lengthen it. The share
intensity cap also restricts vibration, which is stricter than the inspected
stock firmware. The Flipper's independent local cap still applies afterward.

Configuration/control messages close admission, clear pending commands, and stop
and disarm the Flipper while fresh state is obtained. A changed background
snapshot also stops and disarms. An unchanged routine refresh keeps the current
validated state active, including Stop delivery.

After a pause/configuration change, permission change, or connection interruption,
wait for a fresh ready message and physically press **OK** again. If the bridge
has exited, restart it first. Rejected commands are discarded; they do not play
later when you arm. Network commands never arm the app, and there is no automatic
reconnect or operation replay.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Flipper reports an app/API mismatch | Use the build for its existing API, or build with that firmware's SDK. |
| `Configure + connect PC` on the Flipper | Open the app, start the computer bridge, and wait for the ready message before pressing OK. |
| Flipper port cannot be found | Use a USB data cable, open the app first, close other serial programs, and connect only one Flipper for automatic selection. |
| Port is busy or access is denied | Close the other bridge window or serial monitor holding that port. |
| `USB disconnected` or `Computer timed out` | Check the cable and computer power/sleep state, restart the bridge if necessary, then re-arm. |
| `DISARMED` command rejection | Wait for connection readiness and press OK on the Flipper. Send a fresh command; the rejected one is not retried. |
| `LIMIT` command rejection | Lower the requested intensity or deliberately change the local cap while disarmed. |
| `BUSY` command rejection | A nonrepeating request arrived while another operation was active. The running operation is preserved. |
| Identity import fails | Check the original hub's port, claimed state, hub ID, and registered shocker ID. An existing profile is not overwritten. |
| Backend connection ends | Check connectivity and the saved device selection. Restart once the cause is resolved and re-arm; service changes may require a software update. |
| Flipper acknowledges a beep but the shocker is silent | Check power, the selected shocker, pairing, and distance. Keep the original hub unplugged to isolate the radio test. |

For manual port selection, list ports with `host/radio.py list`, identify the
additional radio interface created by the app, and pass that port explicitly:

```powershell
.\.venv\Scripts\python.exe host/standalone.py --port FLIPPER_COM --beep-only
```

Replace `FLIPPER_COM` with the actual radio port, such as `COM7`. The normal
Flipper console port is not the app's radio interface.

## Privacy and supported scope

Keep `.local/` private. Its device identity acts as a credential even though it
is encrypted on disk. Do not attach that folder, decrypted profiles, raw hub
responses, or identifying terminal output to a public issue. A useful report can
include the app/API version, a redacted error category, and steps to reproduce.

Registration uses verified HTTPS. The device's Redis connection uses plain TCP
port 6379, matching the inspected stock protocol; the device credential and
traffic on that connection are not encrypted. This project does not require your
PiShock account password or API key.

The implementation is experimental and supports one selected SmallOne using the
CaiXianlin protocol at 433.920 MHz. Existing Flipper radio permissions remain in
effect. No firmware, region, or transmit-power change is part of setup.

Supported operations last 100–10,000 milliseconds. Repeating commands replace
current output; nonrepeating commands are refused while busy. There is no command
queue. Some legacy command formats and other hub features are not supported.

The shocker sends no delivery acknowledgment. Local time limits, bounded input,
and a USB heartbeat safeguard stop output on detected failures, but the incoming
backend format does not provide a trustworthy original event timestamp. Exact
end-to-end command age cannot be established. Improved range and long-term
reliability require measurement in your setup.

The PiShock device protocol can change independently of this project. Keep the
original hub available: to return to ordinary PiShock operation, stop this bridge,
disarm/exit the Flipper app, and reconnect the original hub.

For development and app builds, see [BUILD.md](BUILD.md). Source attribution and
licensing are in [NOTICE.md](../NOTICE.md) and [LICENSE](../LICENSE).
