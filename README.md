# PiShock Bridge

A Windows desktop app that lets a Flipper Zero transmit commands from your existing PiShock website controls.

**PiShock website → Windows computer → USB → Flipper Zero → your SmallOne shocker**

Set up devices with buttons, choose a paired shocker, and connect. The original hub is needed once to import your own device identity; unplug it during normal use. The computer stays online, awake, and connected to the Flipper by USB.

## Get started

Download **PiShockBridge-Setup-0.2.0.exe** from [Releases](https://github.com/ipechman/pishock-flipper-bridge/releases) when the Windows release is available. Install it, then open **PiShock Bridge** from the Start menu. Python and terminal commands are not needed.

1. In **Set up devices**, find the Flipper and choose **Install app**.
2. Connect your original hub, choose **Find hub**, then **Read paired devices**.
3. Choose your shocker and **Save this device**.
4. Unplug the original hub. Open **PiShock USB Radio** on the Flipper.
5. In **Connection**, choose **Find Flipper**, then **Connect**. Press **OK** on the Flipper and try a short website beep.

Already using the original version? Keep your working Flipper app and choose **Use an existing CLI profile…** to import its encrypted profile.

The [full user guide](docs/USER_GUIDE.md) covers setup, migration, everyday controls, and troubleshooting. It is also available in **Help & about** inside the app.

GitHub's source ZIP is source code, not the Windows installer. Contributors can follow [BUILD.md](docs/BUILD.md) to build the application.

## What it supports

- Windows 10 or 11, 64-bit.
- An existing PiShock Next or Lite hub identity and one selected, already paired SmallOne shocker.
- The bundled Flipper add-on targets **official firmware 1.4.3, API 87.1, hardware f7**. Its installer checks compatibility before copying the app. No firmware installation or formatting is required.
- A compatible PiShock USB Radio app already installed on another API can continue to be used. Skip the installation step; a different API requires its own matching application build.
- Physical arming, a local intensity limit starting at 20%, beep-only commissioning, and a visible stop/disconnect control.

This remains an experimental replacement for the hub's radio and device connection. It needs the computer for internet access. Radio delivery is not acknowledged by the shocker, and some legacy command formats and other hub features are outside its scope.

The new desktop application lives on `main`. The original terminal-based version is preserved on `cli-original`.

## Privacy and acknowledgments

Device profiles are encrypted for the current Windows account and stored outside the application. The distributed project contains no imported device identities, personal addresses, credentials, or live-session logs. Do not share your `device.dpapi` profile.

**Droski1's [PiShock-Unofficial-Documentation](https://github.com/Droski1/PiShock-Unofficial-Documentation) inspired this project.** Thanks also to OpenShock for its CaiXianlin encoder and protocol references, and Flipper Devices for its application SDK and storage protocol.

Independent community software; no affiliation or endorsement by PiShock, Flipper Devices, Droski1, or OpenShock is implied. See [NOTICE.md](NOTICE.md), [LICENSE](LICENSE), and the [build instructions](docs/BUILD.md).
