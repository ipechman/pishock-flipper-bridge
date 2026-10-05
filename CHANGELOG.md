# Change notes

## 0.3.2 — 2026-10-05

- Added a Troubleshoot page with a direct USB beep test using the saved selected target. Each explicit click requests one 0.5-second beep; the diagnostic works without the PiShock website or internet.
- Physical arming is still required. There are no shock or positive-vibration controls, automatic retries, queued repeats, or automatic RF keep-awake in the diagnostic.
- The normal bridge and direct test share one USB session slot. Stop the current session before switching; Stop test, tray Stop, and Exit stop/disarm and release USB.
- Added receiver-pairing troubleshooting and clarified that USB acceptance/Transmitting does not confirm radio reception. Re-pairing restored direct and website beeps during physical troubleshooting of the prior release.
- Existing saved profiles and the 0.3 Flipper add-on remain usable. No firmware change or add-on reinstall is needed for this desktop feature.

The separate standalone controller's implementation plan is under review; its Flipper app is not included in this release.

## 0.3.1 — 2026-10-05

### Connection fix

PiShock registration can close an existing authenticated command connection. The
desktop bridge now completes registration and validates its policy before
opening fresh cloud command connections. This fixes the immediate disconnect
that could occur even after importing the original hub successfully.

### Planned cloud renewal

- Routine renewal, typically every 30 seconds, closes the old connections before
  registration and opens fresh connections afterward. Operation forwarding pauses
  and pending commands are discarded during renewal.
- If any `RUN` or `REPLACE` has been accepted since the last acknowledged `STOP`
  or `DISARM`, the host sends one conservative Stop. An operation may already have
  finished; the Stop is still sent once. The latch clears only after an
  acknowledged Stop or Disarm.
- An unchanged policy preserves the Flipper's existing physical armed or
  disarmed state. Renewal cannot arm a disarmed device.
- Idle renewal sends no `STOP` or `AWAKE`, so the existing maintenance timer keeps
  running. USB heartbeats and enabled zero-output maintenance can continue during
  the planned cloud gap. The extra Stop after the last real operation can delay
  the next keep-awake by roughly one refresh interval once.
- Observed controls, changed policy, and unexpected failures still stop and
  disarm the Flipper and disable maintenance. Unexpected connection loss ends the
  session; the bridge does not automatically retry it or replay commands.

### Protocol limitation

There is a disconnected gap between registration and subscription. Cloud
operations and configuration changes published during that gap can be missed.
Missed operations are never replayed. The next settings refresh revalidates the
policy snapshot; immediate detection of a configuration change during the gap is
not guaranteed.

### Updating and documentation

- Exit the desktop application from its tray menu before updating. Existing saved
  profiles are preserved.
- An installed version 0.3 Flipper add-on can remain in place. This update requires
  no add-on reinstall or firmware change.
- The computer and original hub do not need matching Wi-Fi bands: normal bridge
  operation uses USB to the Flipper and the computer's cloud connection.
- These change notes are included in the desktop package. See the
  [user guide](docs/USER_GUIDE.md) for operation and troubleshooting, and
  [BUILD.md](docs/BUILD.md) for build and validation guidance.

The project remains a Windows computer and USB Flipper bridge. A separate
standalone controller is not included in this release.

### Verification

- All 236 Python tests passed with warnings treated as errors.
- A live 75-second USB and PiShock session completed two routine renewals and
  clean shutdown. Operation forwarding and RF keep-awake were disabled for this
  check; it verified connection stability without operating the shocker.
- Regression tests cover registration closing existing streams, policy changes,
  expired liveness, cancellation, pending-command discard, arming preservation,
  and the keep-awake timer across multiple renewals.

## 0.3.0 — previous release

- Added zero-output idle keep-awake, including while physically disarmed.
- Added the Windows system tray with Open, Stop & disconnect, and Exit actions.
- Simplified the Flipper controls to armed/disarmed and removed the separate
  local intensity cap; validated PiShock settings and permissions apply.

## Acknowledgments

Droski1's [PiShock-Unofficial-Documentation](https://github.com/Droski1/PiShock-Unofficial-Documentation)
inspired this project. See [NOTICE.md](NOTICE.md) for the project's other source
acknowledgments and license information.
