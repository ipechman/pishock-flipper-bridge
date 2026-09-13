# PiShock Bridge user guide

PiShock Bridge connects your existing PiShock website controls to a Flipper Zero over USB. The Flipper sends radio commands to your selected shocker. Keep the computer online and awake while using it.

You need a Windows 10 or 11 computer running 64-bit Windows, a Flipper Zero with an SD card and USB data cable, and a SmallOne shocker already paired to a PiShock Next or Lite hub in your own account. Keep the original hub available for the first setup and any later identity import.

# Install the desktop application

Download PiShockBridge-Setup-0.3.0.exe from the project's GitHub Releases page when the Windows release is available. Run the installer and follow its steps, then open PiShock Bridge from the Start menu. A desktop shortcut is optional. Installing an update preserves your saved device profile.

You do not need Python or a terminal. GitHub's source ZIP contains development files and is not the installer.

Dark mode is the default. To change it, choose Dark or Light under Appearance in the sidebar. Your choice is saved separately from the device profile and restored when you next open the app.

Opening the desktop app does not connect to PiShock or operate a shocker. Start with the shocker powered on and off-body for the connection check.

# Updating from version 0.2

Exit the desktop bridge before running the new Windows installer. In version 0.3, use Exit in the tray menu; the window's X button only hides the window.

After updating the desktop application, open Set up devices and choose Install app to update the Flipper add-on as well. Close the add-on on the Flipper before installing it. This updates only the add-on, keeping your firmware and saved desktop profile.

The new add-on supplies keep-awake and removes the separate local intensity cap. Older add-ons continue accepting website commands, but keep their previous behavior; the desktop app shows an update hint when keep-awake is unavailable.

# If the original version already works for you

You can import your original profile into the desktop interface. Existing add-ons still accept website commands; update the add-on using Install app for version 0.3's keep-awake and armed/disarmed controls. Your firmware stays unchanged.

1. Stop the original bridge and close its window.
2. Open PiShock Bridge and select Set up devices.
3. Choose Use an existing CLI profile….
4. In the file picker, select the device.dpapi file inside the original project's .local folder.
5. Wait for setup to complete, then go to the connection steps below.

The desktop app reads only the file you select. It saves a new encrypted copy for your Windows account and leaves the original profile intact. Use the same Windows account that created the original profile. If it cannot be opened, import again from your original hub.

# Install the Flipper add-on

Skip this section if version 0.3 of PiShock USB Radio is already installed and working on your Flipper.

1. Connect the Flipper to the computer with a USB data cable.
2. Close any app running on the Flipper and return to its main screen. Close qFlipper and other programs using its USB connection.
3. In PiShock Bridge, open Set up devices.
4. Under Install the Flipper app, choose Find Flipper and select the detected device.
5. Choose Install app and keep the cable connected until installation finishes.

The desktop app includes add-on builds for API 87.1 (official firmware 1.4.3) and API 88.9, both for hardware f7. Installation checks your Flipper's API and automatically selects its matching build. You do not need to change firmware. An unsupported API is rejected before writing application files; the contributor build guide explains how to build for other versions.

The installer copies only the add-on and its temporary verification files. It reads the copied application back, verifies it, and keeps a verified backup while replacing an existing copy. Keep power and USB connected through the final copy: Flipper's storage replacement is not guaranteed to be atomic during a power loss.

Installation does not flash firmware, format the SD card, change radio settings, or start the app. When it finishes, open Apps → Sub-GHz → PiShock USB Radio on the Flipper yourself.

# Import your original hub

Skip this section if you have already imported an existing profile.

1. Make sure your original PiShock hub is online and already belongs to your PiShock account.
2. Connect that hub to the computer by USB.
3. In Set up devices, choose Find hub.
4. Select the original hub, then choose Read paired devices.
5. Select the SmallOne shocker you want to use from the list.
6. Choose Save this device and wait for the saved message.
7. Unplug the original hub's USB and power.

Reading and saving the hub performs identity checks without sending an operation to the shocker. The import does not pair a new shocker, claim a new hub, or change ownership. Your selected shocker must already be registered to that hub.

If a device is already saved, the app asks before replacing its desktop profile. Replacing that profile leaves the original hub and any original CLI profile intact. Only one shocker is selected at a time.

The original hub must be online because its reported public address is part of the imported identity. The app keeps this imported address; it does not discover a changed public address automatically.

# Make your first connection

1. Leave the original hub unplugged.
2. Keep the Flipper connected by USB and open PiShock USB Radio on it.
3. In the desktop app, open Connection.
4. Choose Find Flipper and select the Flipper radio app.
5. Leave Beep-only test selected. This mode ignores shock and vibration commands.
6. Check the displayed hub and shocker, then choose Connect.
7. Wait for Connected · arm on Flipper.
8. Press OK on the Flipper to arm it.
9. With the shocker still powered on and off-body, send one short beep from PiShock's website using your existing hub controls.

The Open PiShock button opens the website in your browser. Sign in there if needed; this application does not ask for your PiShock password or API key.

Hearing the beep confirms that the shocker received that test. The application can report a command accepted by the Flipper, but the shocker does not send back a delivery acknowledgment.

Use Back on the Flipper to disarm, or choose Stop & disconnect to end the desktop connection.

# Everyday use

Open PiShock Bridge, open PiShock USB Radio on the USB-connected Flipper, and choose Find Flipper followed by Connect. Keep the original hub unplugged, the application open, and the computer awake.

Beep-only test starts selected whenever you reopen the desktop app. To use the normal supported modes, deselect it while disconnected, then connect. The option is locked during a connection; choose Stop & disconnect before changing it.

Once connected, check the selected target on the Flipper, then press OK to arm. Continue using the existing PiShock website controls. The Flipper has no separate intensity cap: accepted commands use the requested intensity, subject to PiShock permissions and the protocol's normal range.

The desktop app prevents another desktop instance in the same Windows session. Close any original CLI bridge or other program using the Flipper before connecting.

# Keep-awake

While the bridge is connected and ready, the Flipper sends a short zero-output radio packet after approximately 60 seconds of inactivity. It also works while disarmed. Active commands take priority, and activity restarts the inactivity timer.

The packet is the protocol's zero-intensity vibration/stop signal, with no beep or requested stimulation. Although sometimes described as a “0 ms command,” it needs a brief transmission because this radio protocol has no duration field.

Keep-awake stops when you disconnect, exit, lose USB/heartbeat, or the PiShock connection is invalidated. It resumes when the bridge becomes ready again. The computer must remain awake, the shocker powered on, and the shocker in radio range; there is no receiver acknowledgment confirming delivery.

# Keep the bridge in the system tray

The window's X button hides PiShock Bridge in the system tray near the clock. The existing connection and keep-awake continue. If Windows hides the icon, look in the tray's overflow menu.

Double-click the tray icon, or choose Open from its right-click menu, to restore the window. Stop & disconnect ends the connection while leaving the desktop app open. Exit stops and disarms, waits for cleanup, removes the tray icon, and closes the app.

If the tray icon cannot be created or becomes unavailable, the app keeps or restores its window so you can reach the controls. It does not start automatically with Windows.

# Flipper controls

OK while disarmed and connected: arm the application.

OK while armed: stop and disarm.

Back: stop and disarm immediately on the Flipper.

Hold Back: exit the add-on and restore the normal USB connection.

Up and Down no longer adjust an intensity limit. The only operation state is armed or disarmed. The host accepts intensities from 0 to 100; the CaiXianlin radio protocol maps 100 to its maximum value of 99.

PiShock's website Stop cancels current output. Use Back on the Flipper when you also want to disarm it.

# Stopping and reconnecting

Stop & disconnect requests Stop and Disarm, disables keep-awake, closes the bridge connection, and releases USB. Exit from the tray menu performs the same cleanup before closing the application. The window's X button only hides the window and keeps the bridge running.

If the application says that Stop could not be confirmed over USB, press Back on the Flipper directly.

USB loss, loss of the computer heartbeat, or a failed backend connection causes stopping and disarming. Fix the cable, internet connection, or sleep state, then connect again. Press OK on the Flipper and send a fresh command. The bridge does not automatically reconnect or replay commands.

A pause, sharing-permission change, or other configuration update can make the desktop app show Refreshing permissions…. Wait for readiness and physically re-arm on the Flipper. Network commands cannot arm the app.

# Troubleshooting

## The Flipper is not found

For installation, close the app on the Flipper and choose Find Flipper in Set up devices. For a normal connection, open PiShock USB Radio first and choose Find Flipper in Connection. These steps use different USB interfaces.

Use a data cable, close qFlipper and other bridge windows, reconnect USB, and try Find Flipper again. If several devices are listed, select the one you intend to use.

## Installation reports an API mismatch

Version 0.2.0 bundled only API 87.1. If the message says your Flipper uses API 88.9, update the desktop application to 0.2.1 or later and retry Install app; this version includes the matching add-on. Keep your Flipper's firmware as it is.

For APIs other than 87.1 or 88.9, keep an existing working add-on, or use an application build for your Flipper's current firmware. The installer does not change firmware or bypass compatibility checks.

## The hub or paired shocker is missing

Bring the original hub online, connect its USB cable, and choose Find hub again. Close other programs using that port. Check that the hub belongs to your PiShock account and the intended SmallOne is paired and registered, then choose Read paired devices again.

## The Flipper says Configure + connect PC

Open PiShock USB Radio, choose Find Flipper and Connect on the computer, and wait for readiness before pressing OK. An app that has just opened has not yet received its selected target from the bridge.

## The website beep is silent

Check that the shocker is still powered on, the selected target is correct, and the Flipper says it is armed. Keep the shocker in range and the original hub unplugged. Reconnect and send a fresh short beep if needed.

Beep-only test deliberately ignores shock and vibration. To change modes, disconnect first.

## The Flipper reports DISARMED, LIMIT, or BUSY

DISARMED means the command was rejected while unarmed. Wait for readiness, press OK on the Flipper, and send a new command.

LIMIT means an older Flipper add-on is still installed. Version 0.3 removes that local limit; use Set up devices → Install app to update the add-on.

BUSY means a nonrepeating command arrived while another operation was active. The running operation is preserved; rejected requests are not queued.

## Connection fails after the network address changes

Bring the original hub online on the new network and repeat the hub import. Confirm replacement of the desktop profile, then unplug the original hub before connecting again. The bridge retains the public address saved during import.

## A saved profile cannot be opened

Use the Windows account that saved it. A profile is encrypted for that account and should not be treated as a portable credential. On a different computer or account, import from your own original hub again.

# Privacy and updates

Your desktop profile is stored at %LOCALAPPDATA%\PiShockFlipperBridge\device.dpapi. You can paste %LOCALAPPDATA%\PiShockFlipperBridge into File Explorer's address bar to find that folder.

The appearance preference is stored separately in that folder and contains only your theme choice.

The saved identity is encrypted for your Windows account. Wi-Fi passwords and pairing keys are discarded during import. Share the source or installer, not device.dpapi, decrypted profiles, raw hub responses, or screenshots showing your personal device details.

Installing an application update preserves the separate saved profile. Windows Settings can uninstall PiShock Bridge; uninstalling leaves that profile available for a later reinstall. To remove it too, close the app and delete its device.dpapi file using File Explorer.

Registration uses verified HTTPS. The PiShock device connection also uses the original protocol's plain TCP connection, which does not encrypt its credential or traffic. The local profile's encryption does not encrypt that network connection.

The implementation supports one selected SmallOne using the CaiXianlin protocol at 433.920 MHz. Existing Flipper radio permissions remain in effect. Supported operation durations are 100 to 10,000 milliseconds; some older command formats and other hub features are not implemented. Protocol changes at PiShock may require an application update.

The desktop installer and USB file-transfer logic are checked with software tests. Those checks do not establish radio range, receiver delivery, or successful installation on every physical device.

To return to the original hub, choose Stop & disconnect, disarm and exit the Flipper app, then reconnect the original hub.

# Acknowledgments and contributor information

Droski1's [PiShock-Unofficial-Documentation](https://github.com/Droski1/PiShock-Unofficial-Documentation) inspired this project.

OpenShock provided the CaiXianlin encoder and protocol references. Flipper Devices provides the application SDK and USB storage protocol.

This is independent community software. The project source includes [NOTICE.md](../NOTICE.md) and [LICENSE](../LICENSE), with contributor instructions in [BUILD.md](BUILD.md). The desktop application is on main; the original terminal version is preserved on cli-original.
