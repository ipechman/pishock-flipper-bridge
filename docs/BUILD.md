# Building and testing

This project builds a Flipper Zero external application (`.fap`) and includes a
Python bridge for Windows. Building the application does not install or replace
Flipper firmware. Device setup and everyday use are covered in the [README](../README.md).

## Source layout

- `app/`: application manifest, USB interface, and RF encoder.
- `host/`: Windows bridge, profile setup, and Python tests.
- `tests/test_radio_core.c`: portable tests for the RF encoder and command parser.

The supplied `.fap` files are compact, stripped application binaries. Full
application source is included. Personal profiles, credentials, device captures,
build caches, and debug binaries are excluded from the public package.

## Python environment

Use Python 3.13 for the tested build and test environment. From the repository
root, create a virtual environment and install the dependencies. The bridge's
profile storage uses Windows DPAPI, so run the bridge on Windows.

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host/requirements.txt "ufbt==0.2.6"
```

Linux/macOS, for application builds and portable tests:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r host/requirements.txt 'ufbt==0.2.6'
```

[uFBT](https://github.com/flipperdevices/flipperzero-ufbt) downloads the selected
SDK and its required cross compiler on first use. The commands below pin both
uFBT and the SDK version. The verified SDKs use Flipper toolchain version 39.

## Select the matching SDK

Choose the SDK that matches the firmware already installed on the Flipper.
These are the two verified build targets:

| Existing firmware | Hardware target | Firmware API | Supplied application |
| --- | --- | --- | --- |
| [Official 1.4.3](https://github.com/flipperdevices/flipperzero-firmware/releases/tag/1.4.3) | `f7` | `87.1` | `pishock_usb_radio-official-1.4.3.fap` |
| [Unleashed 093](https://github.com/DarkFlippers/unleashed-firmware/releases/tag/unlshd-093) | `f7` | `88.9` | `pishock_usb_radio.fap` |

A different firmware API may require rebuilding against its own SDK. Compatibility
with other SDKs has not been verified.

Keep SDK state local to this checkout. Run the appropriate setup block from the
repository root, then choose **one** of the build blocks below.

Windows PowerShell:

```powershell
$env:UFBT_HOME = Join-Path $PWD ".ufbt-build"
$env:PATH = (Join-Path $PWD ".venv\Scripts") + [IO.Path]::PathSeparator + $env:PATH
Set-Location app
```

Linux/macOS:

```sh
export UFBT_HOME="$PWD/.ufbt-build"
export PATH="$PWD/.venv/bin:$PATH"
cd app
```

Build for official firmware 1.4.3:

```sh
ufbt update --branch=1.4.3 --hw-target=f7
ufbt
```

Or build for Unleashed 093 using its fixed SDK archive:

```sh
ufbt update --url=https://github.com/DarkFlippers/unleashed-firmware/releases/download/unlshd-093/flipper-z-f7-sdk-unlshd-093.zip --hw-target=f7
ufbt
```

The result is `app/dist/pishock_usb_radio.fap`, relative to the repository root.
Both targets produce that same filename. Copy the official build to a separate
file before building the Unleashed variant if you need both. uFBT also produces
debug artifacts under `app/dist/debug/`; keep those local. Source builds can
contain compiler-specific metadata and need not be byte-for-byte identical to
the supplied binaries.

## Run the tests

Run these commands from the repository root. The Python suite uses synthetic
identities and mocked serial/network connections. It does not need a hub,
Flipper, account, or saved profile.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s host -p "test_*.py" -v
```

Linux/macOS:

```sh
.venv/bin/python -B -m unittest discover -s host -p 'test_*.py' -v
```

The DPAPI round-trip test runs on Windows and is skipped on other platforms.
The suite checks parsing, permissions and pause handling, transport failures,
profile handling, and bridge behavior.

For the portable C tests, use a native C11 compiler such as GCC on Linux:

```sh
mkdir -p build
gcc -std=c11 -Wall -Wextra -Werror app/radio_core.c tests/test_radio_core.c -o build/test_radio_core
./build/test_radio_core
```

These tests check independent RF vectors, finite pulse timing over the supported
duration range, and strict parsing of accepted and rejected USB commands. Keep
assertions enabled when compiling the tests.

The [GitHub Actions workflow](../.github/workflows/tests.yml) runs the full Python
suite on Windows with Python 3.13 and the portable C tests on Ubuntu. It does not
build or install firmware, contact PiShock, or publish artifacts. Passing these
tests does not verify real RF range or compatibility with an untested shocker.
