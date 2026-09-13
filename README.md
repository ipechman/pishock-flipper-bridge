# PiShock Bridge

A Windows desktop app that lets a Flipper Zero transmit commands from your existing PiShock website controls.

**PiShock website → Windows computer → USB → Flipper Zero → your SmallOne shocker**

Set up devices with buttons, choose a paired shocker, and connect. The original hub is needed once to import your own device identity; unplug it during normal use. The computer stays online, awake, and connected to the Flipper by USB.

## Get started

Download **PiShockBridge-Setup-0.3.0.exe** from [Releases](https://github.com/ipechman/pishock-flipper-bridge/releases) when the Windows release is available. Install it, then open **PiShock Bridge** from the Start menu. Python and terminal commands are not needed.

1. In **Set up devices**, find the Flipper and choose **Install app**.
2. Connect your original hub, choose **Find hub**, then **Read paired devices**.
3. Choose your shocker and **Save this device**.
4. Unplug the original hub. Open **PiShock USB Radio** on the Flipper.
5. In **Connection**, choose **Find Flipper**, then **Connect**. Press **OK** on the Flipper and try a short website beep.

Already using the original version? Keep your working Flipper app and choose **Use an existing CLI profile…** to import its encrypted profile.

**Updating from 0.2?** Exit the old desktop app, install this update, then use **Set up devices → Install app** to update the Flipper add-on too. Your saved profile is preserved. The new add-on is required for keep-awake and the simplified armed/disarmed controls.

The desktop app opens in dark mode by default. Choose **Dark** or **Light** under **Appearance** in the sidebar; your choice is remembered on this computer.

Closing the window keeps the bridge running in the **system tray near the clock**. Use the tray icon to reopen it, **Stop & disconnect**, or **Exit** completely.

The [full user guide](docs/USER_GUIDE.md) covers setup, migration, everyday controls, and troubleshooting. It is also available in **Help & about** inside the app.

GitHub's source ZIP is source code, not the Windows installer. Contributors can follow [BUILD.md](docs/BUILD.md) to build the application.

## What it supports

- Windows 10 or 11, 64-bit.
- An existing PiShock Next or Lite hub identity and one selected, already paired SmallOne shocker.
- Bundled Flipper add-ons support **API 87.1 (official firmware 1.4.3)** and **API 88.9**, both on hardware **f7**. The installer detects your API and selects the matching build before copying it. Custom firmware is not required, and installation does not change your firmware or format storage.
- A compatible PiShock USB Radio app already installed can continue to be used. Skip installation if it is already working. Other APIs need a matching application build; the installer does not override an incompatibility.
- Physical armed/disarmed controls, beep-only commissioning, and visible stop/disconnect controls. There is no separate Flipper intensity cap; the validated PiShock command settings and permissions apply.
- Automatic zero-output radio keep-alives after a minute of inactivity while connected, including while disarmed. They defer during active output and stop when the bridge disconnects or its connection is being revalidated.

This remains an experimental replacement for the hub's radio and device connection. It needs the computer for internet access. Radio delivery is not acknowledged by the shocker, and some legacy command formats and other hub features are outside its scope.

The new desktop application lives on `main`. The original terminal-based version is preserved on `cli-original`.

## Privacy and acknowledgments

Device profiles are encrypted for the current Windows account and stored outside the application. The distributed project contains no imported device identities, personal addresses, credentials, or live-session logs. Do not share your `device.dpapi` profile.

**Droski1's [PiShock-Unofficial-Documentation](https://github.com/Droski1/PiShock-Unofficial-Documentation) inspired this project.** Thanks also to OpenShock for its CaiXianlin encoder and protocol references, and Flipper Devices for its application SDK and storage protocol.

Independent community software; no affiliation or endorsement by PiShock, Flipper Devices, Droski1, or OpenShock is implied. See [NOTICE.md](NOTICE.md), [LICENSE](LICENSE), and the [build instructions](docs/BUILD.md).
