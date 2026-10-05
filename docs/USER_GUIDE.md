# PiShock Bridge user guide

PiShock Bridge connects your existing PiShock website controls to a Flipper Zero over USB. The Flipper sends radio commands to your selected shocker. Keep the computer online and awake for website bridge use. Version 0.4.0 also includes a separate PiShock Controller add-on for offline operation from the Flipper after graphical setup.

You need a Windows 10 or 11 computer running 64-bit Windows, a Flipper Zero with an SD card and USB data cable, and a SmallOne shocker already paired to a PiShock Next or Lite hub in your own account. Keep the original hub available for the first setup and any later identity import.

# Install the desktop application

Download PiShockBridge-Setup-0.4.0.exe from the project's GitHub Releases page when the Windows release is available. Run the installer and follow its steps, then open PiShock Bridge from the Start menu. A desktop shortcut is optional. Installing an update preserves your saved device profile.

You do not need Python or a terminal. GitHub's source ZIP contains development files and is not the installer.

Dark mode is the default. To change it, choose Dark or Light under Appearance in the sidebar. Your choice is saved separately from the device profile and restored when you next open the app.

Opening the desktop app does not connect to PiShock or operate a shocker. Start with the shocker powered on and off-body for the connection check.

# Updating from version 0.3, 0.3.1, or 0.3.2

Choose Exit from the desktop app's tray menu, then install version 0.4.0. Your saved profile is preserved. Keep the existing version 0.3 PiShock USB Radio add-on: this desktop update requires no add-on reinstall or Flipper firmware change. Version 0.4.0 adds the separate standalone controller described below. Version 0.3.2 added a direct USB beep diagnostic under Troubleshoot.

Version 0.3.1 fixes an immediate connection failure by completing PiShock registration before opening the cloud command connections. Registration can close connections that are already open. The [change notes](../CHANGELOG.md) document the fix and planned renewal behavior.

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

The computer and original hub do not need to use the same Wi-Fi band. Each needs internet access when it is being used. During normal bridge use, the Flipper connects to the computer by USB and the computer connects to PiShock over the internet; the original hub stays unplugged.

# Make your first connection

1. Leave the original hub unplugged.
2. Keep the Flipper connected by USB and open PiShock USB Radio on it.
3. In the desktop app, open Connection.
4. Choose Find Flipper and select the Flipper radio app.
5. Leave Beep-only test selected. This mode ignores shock and vibration commands.
6. Check the displayed hub and shocker, then choose Connect.
7. Wait for Connected · check Flipper.
8. Press OK on the Flipper to arm it.
9. With the shocker still powered on and off-body, send one short beep from PiShock's website using your existing hub controls.

The Open PiShock button opens the website in your browser. Sign in there if needed; this application does not ask for your PiShock password or API key.

Hearing the beep confirms that the shocker received that test. The application can report a command accepted by the Flipper, but the shocker does not send back a delivery acknowledgment.

Use Back on the Flipper to disarm, or choose Stop & disconnect to end the desktop connection.

# Standalone PiShock Controller

PiShock Controller is a separate add-on for local operation without USB, a computer, internet, or the original hub after setup. It does not replace PiShock USB Radio, flash firmware, or require custom firmware. Both bundled controller builds target hardware f7: API 87.1 (official firmware 1.4.3) and API 88.9. Installation detects and selects the matching build.

## Install and assign your saved target

1. Import and save your selected shocker using Set up devices, or use your existing saved profile.
2. Stop the bridge or direct test. Connect the Flipper by USB, close its running apps, and close qFlipper or other programs using USB.
3. In Set up devices, choose Find Flipper and select it, then choose Install standalone controller.
4. Keep USB connected until the app and target have both been copied, read back, and verified. Installation does not launch the controller or transmit.
5. Disconnect USB and open Apps → Sub-GHz → PiShock Controller. Check the displayed target before arming.

The selected saved shocker's ID and channel are assigned automatically. You do not need a terminal. To select a different shocker, save that selection in the bridge, close the controller, and choose Update controller target. This changes the target without reinstalling the app; reopen the controller to read it.

The generic public application is stored at /ext/apps/Sub-GHz/pishock_controller.fap. The only personal controller settings are ID (1–65535) and channel (0–2), in /ext/apps_data/pishock_controller/target.conf. This readable SD-card file is not encrypted. It contains no full Windows profile, account credentials, Wi-Fi settings, or hub identity. Do not share a real target file.

If installation reports a partial result, repeat the failed step before using the controller. A copied app with an unverified or stale target is not ready. Keep USB and power connected during copying; storage replacement is not guaranteed atomic. Closing the desktop waits for the current verified-file operation to finish or restore before exiting. Missing, invalid, oversized, duplicate-field, unsupported-version, or out-of-range target configuration prevents arming and shows a setup instruction. Use Update controller target to repair it.

## Controller buttons and operation

Every launch starts disarmed with Beep, intensity 0%, and duration 0.5 seconds. Mode, intensity, duration, and armed state are not saved.

- Up/Down selects Mode, Intensity, or Duration. Left/Right changes the selected value; button repeat can adjust settings.
- Modes are Beep, Vibration, and Shock. Beep always uses zero intensity. Vibration and Shock allow 0–100%; the protocol maps 100% to its supported maximum of 99.
- Duration is 0.1–10.0 seconds, in 0.1-second steps.
- Hold physical OK to arm, release it, then make a fresh physical OK press to send one timed operation. Holding OK to arm does not send an operation.
- After completion, arming stays active. Changing a setting disarms; settings cannot change during transmission. Repeated or queued input cannot start repeated operations.
- Back immediately stops transmission and disarms. Hold Back to exit after stopping. Relaunch always starts disarmed.

The screen reports local transmission and completion, not receiver acknowledgment. There is no operation queue, automatic retry, remote arming, or automatic keep-awake. PiShock website pause controls, sharing permissions, and cloud settings do not govern offline controller use. USB Radio's website permissions and keep-awake continue to apply only to that separate bridge session.

## Check reception and pairing with a beep

Keep the powered receiver off-body, near the Flipper, and check the displayed target. Leave Beep selected at 0.5 seconds. Physically hold OK to arm, release, and press OK once. Listen for the receiver response; use Back to disarm afterward.

If silent, check charge, range, target selection, and pairing. Importing the hub copies identity but does not pair the receiver. For a SmallOne pairing check, keep the original hub unplugged, prepare the controller in Beep mode and arm it, then put the receiver into its pairing window as described in the existing direct-beep troubleshooting section. Make one fresh OK press during that window, then test one beep in normal mode and disarm. The direct-beep pairing procedure was verified with USB Radio; standalone controller hardware pairing and delivery remain pending until separately tested.

To check the add-on coexistence, exit the controller with long Back, reconnect USB, and open PiShock USB Radio for the existing desktop connection. USB Radio uses a single OK press to arm and website or desktop commands to send; the controller uses hold OK, release, then a fresh OK press to send locally.

# Everyday use

Open PiShock Bridge, open PiShock USB Radio on the USB-connected Flipper, and choose Find Flipper followed by Connect. Keep the original hub unplugged, the application open, and the computer awake.

Beep-only test starts selected whenever you reopen the desktop app. To use the normal supported modes, deselect it while disconnected, then connect. The option is locked during a connection; choose Stop & disconnect before changing it.

Once connected, check the selected target on the Flipper, then press OK to arm. Continue using the existing PiShock website controls. The Flipper has no separate intensity cap: accepted commands use the requested intensity, subject to PiShock permissions and the protocol's normal range.

The desktop app prevents another desktop instance in the same Windows session. Close any original CLI bridge or other program using the Flipper before connecting.

# Routine PiShock renewal

The bridge renews its cloud connection and checks its current settings, typically every 30 seconds. During this planned renewal it pauses operation forwarding and discards any pending command. If a website operation has been accepted by the Flipper since the last confirmed Stop or Disarm, the bridge sends one Stop before resuming. This also covers an operation that may have finished already. Pending or missed commands are never replayed; use a fresh website command after readiness returns.

If the settings are unchanged, renewal preserves the Flipper's existing armed or disarmed state. It cannot arm a disarmed Flipper. USB heartbeats and enabled zero-output keep-awake continue during the brief cloud renewal.

An observed cloud control change, changed settings, or an unexpected failure still stops and disarms the Flipper and disables keep-awake. Wait for readiness and physically re-arm after a settings change. An unexpected connection failure ends the session and requires you to connect again.

There is a short gap between registration and subscribing to new cloud messages. Operations and configuration changes sent during that gap can be missed. Missed operations are discarded permanently, and the next settings refresh revalidates the current policy snapshot. The bridge cannot guarantee immediate observation of a configuration change during this gap.

# Website bridge keep-awake

While the bridge is connected and ready, the Flipper sends a short zero-output radio packet after approximately 60 seconds of inactivity. It also works while disarmed. Active commands take priority, and activity restarts the inactivity timer.

The packet is the protocol's zero-intensity vibration/stop signal, with no beep or requested stimulation. Although sometimes described as a “0 ms command,” it needs a brief transmission because this radio protocol has no duration field.

Idle routine renewals send neither Stop nor another keep-awake enable command, so the inactivity timer continues. After a real operation, the single conservative Stop at the next renewal can delay the next keep-awake by roughly one refresh interval once. Later idle renewals do not keep postponing it.

Keep-awake stops when you disconnect, exit, lose USB/heartbeat, encounter an unexpected cloud failure, or the bridge observes a control change or changed settings. It resumes when the bridge becomes ready again. It can remain enabled during planned cloud renewal. The computer must remain awake, the shocker powered on, and the shocker in radio range; there is no receiver acknowledgment confirming delivery.

# Keep the bridge in the system tray

The window's X button hides PiShock Bridge in the system tray near the clock. The existing connection and keep-awake continue. If Windows hides the icon, look in the tray's overflow menu.

Double-click the tray icon, or choose Open from its right-click menu, to restore the window. Stop & disconnect ends the connection while leaving the desktop app open. Exit stops and disarms, waits for cleanup, removes the tray icon, and closes the app.

If the tray icon cannot be created or becomes unavailable, the app keeps or restores its window so you can reach the controls. It does not start automatically with Windows.

# PiShock USB Radio controls

OK while disarmed and connected: arm the application.

OK while armed: stop and disarm.

Back: stop and disarm immediately on the Flipper.

Hold Back: exit the add-on and restore the normal USB connection.

Up and Down no longer adjust an intensity limit. The only operation state is armed or disarmed. The host accepts intensities from 0 to 100; the CaiXianlin radio protocol maps 100 to its maximum value of 99.

PiShock's website Stop cancels current output. Use Back on the Flipper when you also want to disarm it.

# Stopping and reconnecting

Stop & disconnect requests Stop and Disarm, disables keep-awake, closes the bridge connection, and releases USB. Exit from the tray menu performs the same cleanup before closing the application. The window's X button only hides the window and keeps the bridge running.

If the application says that Stop could not be confirmed over USB, press Back on the Flipper directly.

USB loss, loss of the computer heartbeat, or an unexpected failed backend connection causes stopping and disarming. Fix the cable, internet connection, or sleep state, then connect again. Press OK on the Flipper and send a fresh command. The bridge does not automatically retry an unexpected connection failure or replay commands.

A pause, sharing-permission change, or other configuration update can make the desktop app show Refreshing permissions…. Wait for readiness and physically re-arm on the Flipper. Network commands cannot arm the app.

# Troubleshooting

## Direct beep test

This diagnostic bypasses the website and cloud connection. It uses the same saved shocker ID and channel as the bridge. Preparing it only configures USB; a beep is sent only when you click the beep button.

1. Stop and disconnect the normal bridge. Unplug the original hub and close other programs using the Flipper.
2. Keep the powered shocker off your body near the Flipper. Open PiShock USB Radio on the Flipper.
3. Open Troubleshoot, choose Find Flipper, select the radio interface, then choose Prepare USB test.
4. Press OK on the Flipper to arm it, then choose Send one 0.5-second beep.
5. Listen for the shocker's response. An accepted USB command confirms transmitter startup, not reception.
6. Choose Stop test before returning to Connection and reconnecting the normal bridge. Arm again on the Flipper before sending a fresh website command.

The test has no shock or positive-vibration controls, automatic retransmission, or RF keep-awake. Rapid extra clicks are ignored while a beep is pending or the brief cooldown is active. A disarmed rejection is not replayed after you arm. Back on the Flipper remains the immediate stop control; Stop test and Exit also stop and disarm. Closing the desktop window keeps the current session in the tray, so use Stop test or Exit when finished.

### If USB accepts the beep but the receiver is silent

Power the receiver off and back on and confirm its power-on indication. Check the selected target, distance, charge, and pairing. Importing a hub profile copies its settings; it does not put the physical receiver into pairing mode.

For a SmallOne pairing check, keep the original hub unplugged and prepare the direct test first. With the receiver powered on, hold its power button for about three seconds to enter its ten-second pairing window, then click the direct beep button once. Successful pairing produces an audible/physical confirmation. Try another beep afterward in normal mode, then stop the test and check a website beep through the bridge. This direct-beep pairing sequence restored reception in the project's physical troubleshooting test. PiShock's [official pairing instructions](https://docs.pishock.com/faqs.html) describe the receiver's pairing window and use a vibration command from the original hub.

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

Use the separate Direct beep test above to distinguish the USB/radio path from website forwarding. Neither a bridge log entry nor the Flipper's Transmitting label confirms receiver delivery.

## The Flipper reports DISARMED, LIMIT, or BUSY

DISARMED means the command was rejected while unarmed. Wait for readiness, press OK on the Flipper, and send a new command.

LIMIT means an older Flipper add-on is still installed. Version 0.3 removes that local limit; use Set up devices → Install app to update the add-on.

BUSY means a nonrepeating command arrived while another operation was active. The running operation is preserved; rejected requests are not queued.

## Connection fails after the network address changes

Bring the original hub online on the new network and repeat the hub import. Confirm replacement of the desktop profile, then unplug the original hub before connecting again. The bridge retains the public address saved during import.

## Connect immediately reports that the connection ended

Update the desktop application to version 0.3.1. It fixes the registration order that could immediately close a newly opened PiShock connection, even after a successful hub import. An existing version 0.3 Flipper add-on does not need reinstalling for this fix.

The connection-ended message can also indicate a USB or internet failure. If it persists after updating, check that PiShock USB Radio is open, other USB programs are closed, the computer is online, and the original hub is unplugged. A successful website command through the original hub does not by itself verify the computer's bridge connection.

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

This is independent community software. The project source includes [NOTICE.md](../NOTICE.md), [LICENSE](../LICENSE), and [change notes](../CHANGELOG.md), with contributor instructions in [BUILD.md](BUILD.md). The desktop application is on main; the original terminal version is preserved on cli-original.
