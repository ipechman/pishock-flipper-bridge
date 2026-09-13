# Building and testing

End users should download and run the Windows installer. They do not need Python,
build tools, or a terminal. The [usage guide](USER_GUIDE.md) covers installation,
Flipper setup, and everyday operation. This document is for contributors who want
to build the desktop application, run tests, or rebuild the Flipper add-on.

## Source layout

- `host/desktop_app.py`: desktop window and guided setup.
- `host/desktop_service.py`, `desktop_paths.py`, and `flipper_install.py`: desktop
  coordination, private settings, and installation of the Flipper add-on.
- Other `host/` modules: PiShock bridge, USB connection, and unit tests.
- `app/`: external Flipper application manifest, USB interface, and RF encoder.
- `tests/test_radio_core.c`: portable RF encoder and command parser tests.
- `packaging/`: Windows build, release privacy audit, and installer definition.

## Build the Windows desktop release

Use 64-bit Python 3.13 on Windows 10 or 11. The release packages include Python,
Tk, and pyserial, so those dependencies do not need to be installed by end users.
The build creates a windowed executable without a command prompt, a portable ZIP,
and an installer with Start menu and optional desktop shortcuts. Installation is
per user and does not require administrator access.

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\packaging\fetch_inno.ps1 -Destination .\build\tooling
.\.venv\Scripts\python.exe -B packaging/build_windows.py --work-dir build/desktop --output-dir dist --inno-compiler build/tooling/inno/ISCC.exe
```

The compiler preparation script downloads the official Inno Setup 6.7.3 package,
checks its pinned SHA-256 and Windows signature, and uses its documented
[portable mode](https://jrsoftware.org/ishelp/topic_technotes.htm). It keeps the
compiler under `build/tooling`, without registering a system installation. An
existing compatible Inno Setup compiler can also be passed to `--inno-compiler`.
Omit that argument to build just the portable application and ZIP.

The release files appear in `dist/`:

- `PiShockBridge-Setup-0.2.0.exe`: the end-user installer.
- `PiShockBridge-0.2.0-windows-x64.zip`: portable application; extract the entire
  folder and open `PiShockBridge.exe`.
- `SHA256SUMS.txt`: checksums for the downloadable packages.
- `BUILD_INFO.json`: tool versions and build validation results.

The extracted application directory is also available for local testing.
Do not commit generated release packages to source control; attach the reviewed
installer and ZIP to a release when publishing. Builds are unsigned unless a
maintainer separately applies a code-signing certificate. The build scripts do
not contain a certificate, signing secret, or automatic publication step.

### What the release build checks

The packager copies an explicit list of public runtime modules and resources to
a fresh staging directory. It includes the official firmware add-on, guide,
README, license, and notices. It excludes profiles, settings, logs, captures,
tests, Git history, local tools, and compiler debug artifacts.

PyInstaller 6.21.0 anonymizes source filenames in its collected Python bytecode.
The build also opens the executable's compressed Python archives and standard
library ZIP to check every nested code object for absolute source filenames.
It scans release files for the builder's home and checkout paths, rejects private
profile and debug artifacts, and verifies that the executable uses the Windows
GUI subsystem. This verifies build-path hygiene; it does not replace reviewing
the source for private information before publishing.

After that audit, the packaged executable runs `--self-test`. This verifies
imports, bundled resources, and creation of a hidden Tk window without reading a
profile, enumerating USB devices, connecting to PiShock, or transmitting RF.
The packaging runtime hook initializes the bundled Tcl 8.6 library before the
first Tk window; this is needed by some Python distributions when frozen.

For an additional local privacy check, run:

```powershell
.\.venv\Scripts\python.exe -B packaging/audit_bundle.py dist/PiShockBridge-0.2.0-windows-x64
```

The optional `--forbid-text` argument checks additional private values without
printing any matching value. Avoid committing commands or reports containing
those private values.

## Run the tests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s host -p "test_*.py" -v
```

Tests use synthetic identities and mocked serial/network connections. They do
not need a hub, Flipper, account, or saved profile. The DPAPI round-trip test runs
on Windows and is skipped on other platforms. The suite checks parsing,
permissions, pause handling, transport failures, private profile handling, and
bridge behavior.

For the portable C tests, use a native C11 compiler such as GCC on Linux:

```sh
mkdir -p build
gcc -std=c11 -Wall -Wextra -Werror app/radio_core.c tests/test_radio_core.c -o build/test_radio_core
./build/test_radio_core
```

These tests check independent RF vectors, finite pulse timing, and strict parsing
of accepted and rejected USB commands. Keep assertions enabled when compiling.
Passing these tests does not verify RF range or an untested shocker model.

## Rebuild the Flipper add-on

The add-on is a normal external `.fap` application. Custom firmware is not
required. Building or installing this add-on does not replace Flipper firmware.
The supplied `pishock_usb_radio-official-1.4.3.fap` targets official firmware
1.4.3, hardware `f7`, API `87.1`. Other firmware APIs may require a matching build.

Install [uFBT](https://github.com/flipperdevices/flipperzero-ufbt), then build with
the official SDK from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install "ufbt==0.2.6"
$env:UFBT_HOME = Join-Path $PWD '.ufbt-build'
$env:PATH = (Join-Path $PWD '.venv\Scripts') + [IO.Path]::PathSeparator + $env:PATH
Set-Location app
ufbt update --branch=1.4.3 --hw-target=f7
ufbt
```

uFBT downloads the selected SDK and compiler on first use. The resulting add-on
is `app/dist/pishock_usb_radio.fap`, relative to the repository root. Copy the
release `.fap` to `pishock_usb_radio-official-1.4.3.fap` only after checking that it
matches the documented target. Keep `app/dist/debug/` and other debug artifacts
local. Rebuild the desktop release to include a changed add-on.

## GitHub Actions

The [workflow](../.github/workflows/tests.yml) runs Python tests on Windows and C
tests on Ubuntu for pushes and pull requests, with read-only repository access.
To build Windows downloads, use **Actions → Tests and desktop build → Run
workflow**, select **Build the Windows installer and portable application**, and
run it. The build waits for both test jobs, checks the packaged application, and
saves downloadable artifacts for 14 days. It does not create a release, push
commits, contact PiShock, or install firmware.
