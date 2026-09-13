# PiShock Flipper USB Bridge

Use a Flipper Zero as the radio transmitter for your existing PiShock hub identity:

**PiShock website → Windows computer → USB → Flipper Zero → SmallOne shocker**

The original PiShock hub is needed once to import its identity. After setup, leave
it unplugged: the computer connects to PiShock and forwards commands to the Flipper
app. The computer must remain connected to the internet and the Flipper by USB.

This is an experimental community project. It supports one selected SmallOne
shocker and preserves its registered hub, website controls, and supported share
permissions. It does not create a new PiShock hub or account.

## Getting started

You need Windows, Python 3.10 or newer, an already claimed PiShock Next/Lite hub
with a paired SmallOne, and a Flipper Zero. Install the app build that matches the
Flipper's **existing firmware/API**; no firmware change is required.

Follow the [user guide](docs/USER_GUIDE.md) to:

1. Install the matching Flipper app and Python dependency.
2. Import your own hub identity over USB.
3. Unplug the original hub and test the bridge in beep-only mode.
4. Start **Start standalone.cmd**, wait for **Direct connection ready**, and
   physically press **OK** on the Flipper to arm.

The app starts disarmed with a **20% local intensity cap**. **Back** stops and
disarms it; **Ctrl+C** stops the computer bridge. Connection failures and permission
changes require physical re-arming. Network commands cannot arm the Flipper.

## Documentation and source

- [Setup, daily use, and troubleshooting](docs/USER_GUIDE.md)
- [Building the Flipper app](docs/BUILD.md)
- `app/`: Flipper application source
- `host/`: standalone computer bridge, identity importer, and offline tests
- [License](LICENSE) and [attribution](NOTICE.md)

Run the host tests after creating the environment described in the guide:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s host -p "test_*.py"
```

The encrypted `.local/` device profile is private and excluded from version
control. PiShock's device protocol is undocumented and can change. Receiver
delivery and improved range are not guaranteed; see the guide's limitations.

Independent project; not affiliated with PiShock or Flipper Devices. Vendor hub
firmware is not distributed with this project.
