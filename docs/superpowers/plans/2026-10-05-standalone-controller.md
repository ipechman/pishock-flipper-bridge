# Standalone PiShock Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Install and automatically configure a separate offline Flipper controller from the desktop bridge, using the selected shocker's ID and channel.

**Architecture:** Keep one canonical RF encoder and add a portable controller state model plus a Flipper GUI/HAL adapter. Extend the existing verified USB file installer to install the matching controller build and a small target-only configuration; the desktop uses the current profile and never builds personalized public binaries.

**Tech Stack:** C11, Flipper f7 SDK APIs 87.1 and 88.9, Python 3.13, Tk, existing serial storage CLI, existing Windows packaging.

**Spec:** `docs/superpowers/specs/2026-10-05-standalone-controller-design.md`

## Global Constraints

- Add-on only: no firmware flashing, formatting, or USB Radio replacement.
- Official firmware 1.4.3/API 87.1 and API 88.9, hardware f7.
- Starts disarmed, Beep, zero intensity, duration 0.5 seconds; duration range 0.1–10.0 seconds in 0.1-second steps, intensity 0–100%.
- Hold OK to arm, release, then a fresh physical OK press sends one timed operation. Settings changes disarm; no queued/repeated operations.
- Back stops/disarms; long Back exits after stopping. Launch never restores armed state.
- Target ID 1–65535 and channel 0–2 are the only personal settings copied to the controller. Never package real target files or complete profiles.
- No cloud connection, automatic keep-awake, remote arming, or automatic retry in the controller.
- Keep local changes/builds, preserve `cli-original` and prior release artifacts, acknowledge Droski1, and provide manual publishing commands.

## Review Focus

- Delayed or duplicated hardware input after an operation must not become a new operation: Task 1 pins sequence and timestamp rejection.
- Back or queue overflow concurrent with OK must stop/disarm with priority: Tasks 1–2 pin stop precedence and reject stale queued input.
- A valid application with a stale target after partial installation must not be reported as ready: Tasks 3–4 pin partial-result messaging and target readback.
- Closing the desktop during file copying must complete or restore the current verified-file operation: Tasks 3–4 pin cancellation and Exit behavior.
- Corrupt, oversized, duplicate-field or unsupported-version SD configuration must leave the controller unable to arm: Tasks 1–2 pin strict bounded parsing.

---

### Task 1: Portable controller behavior and target parser

**Files:** Create `controller/controller_core.h`, `controller/controller_core.c`, `tests/test_controller_core.c`; modify `.github/workflows/tests.yml` to run the portable tests alongside the RF encoder tests.

**Interfaces:**
- Produces `ControllerTarget { uint16_t id; uint8_t channel; }` and `bool controller_parse_target(const char* text, size_t length, ControllerTarget* out)`.
- Produces `ControllerState` with configured target, mode (`b`, `v`, `s`), intensity, duration_ms, selection, phase, armed state, monotonic millisecond deadline, and input sequence tracking.
- Produces `controller_init(ControllerState*, const ControllerTarget*)`, `controller_input(ControllerState*, ControllerInput, uint32_t now_ms)`, `controller_tick(ControllerState*, uint32_t now_ms, bool tx_complete)`, and `controller_tx_failed(ControllerState*)`.
- `ControllerInput` carries key, Press/Release/Long/Repeat type, physical-source boolean, hardware sequence counter, and event timestamp. Model functions return an action bitmask: start selected operation, halt, start zero terminator, exit. HAL adapter consumes those actions synchronously.

- [x] **Write failing C tests** asserting initial disarmed/Beep/0/500 state; invalid target cannot arm; Long OK without a matching fresh hardware Press cannot arm; Press+Long+Release arms without operation; next distinct Press starts once; duplicate sequence/Repeat/software start/stale event (>250 ms) causes zero starts; changing a setting disarms; settings frozen while operating; Back dominates; deadline and uint32 wrap end once; terminator completes before exit; failed TX disarms. Assert duration clamps at 100 and 10000 ms, step 100, intensity clamps 0/100, and Beep always transmits zero.
- [x] **Write parser tests** for canonical `Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 12345\nChannel: 0\n`, LF/CRLF, ID/channel boundaries, zero/negative/overflow, duplicate or missing keys, unsupported version, junk fields, embedded NUL and length over 256 bytes. Failed parses must leave `out` unchanged.
- [x] **Run RED** with `gcc -std=c11 -Wall -Wextra -Werror controller/controller_core.c tests/test_controller_core.c -o build/test_controller_core`; absence of implementation must fail. Use local MSVC `/std:c11 /W4 /WX` where GCC is unavailable.
- [x] **Implement the interfaces** with unsigned wrap-safe elapsed-time comparisons, 1% intensity adjustment, fresh sequence tracking, and a 300 ms zero terminator. Reject inputs captured before the most recent operation finishes. No dynamic allocation or SDK dependencies in the core.
- [x] **Run GREEN** for both portable C test executables; add the same strict controller build/run to the existing Linux CI job.
- [x] **Commit** the model, tests and CI update with a descriptive local commit after review.

### Task 2: Flipper application and reproducible builds

**Files:** Create `controller/pishock_controller.c`, `controller/application.fam`, `packaging/build_controller.py`; create `pishock_controller-official-1.4.3.fap` and `pishock_controller-api-88.9.fap`; record their SHA-256 hashes in `docs/BUILD.md` alongside the existing USB Radio asset hashes.

**Interfaces:**
- Consumes Task 1's state model and parser, and canonical `app/radio_core.h`/`.c` (`radio_sequence_init`, `radio_sequence_next`, `RADIO_TERMINATOR_MS`).
- Produces app ID `pishock_controller`, entry point `int32_t pishock_controller_app(void* argument)` and category Sub-GHz.
- Build entry: `python packaging/build_controller.py --sdk <existing SDK directory> --toolchain <existing toolchain directory> --work-dir <staging directory> --output <FAP destination>`; no dependency downloads or access to user profiles.

- [x] **Pin adapter verification before implementation:** Task 1 exercises input and deadline behavior; both SDK builds and actual FAP metadata/import inspection verify integration. Review separate app ID, fixed target path, hardware sequence-source check, bounded queue, halt-before-buffer-replacement, balanced charge suppression and cleanup directly. Avoid source-text tests that merely mirror the implementation.
- [x] **Implement storage loading** for `/ext/apps_data/pishock_controller/target.conf`, bounded to 256 bytes using storage APIs, then call `controller_parse_target`. Missing or rejected data displays a bridge setup instruction and cannot arm.
- [x] **Implement the GUI/input adapter** with copied display state under a mutex, target/channel always visible, three selectable settings, state and control hints. Timestamp callback events; give Back/overflow priority using thread flags; discard inputs captured while transmitting and require a new release/press sequence before a subsequent operation. Only hardware events arm/start; software Back can stop.
- [x] **Implement finite RF output** using the existing 433920000 Hz OOK preset and encoder. Stop asynchronous TX before rewriting the sequence; bound output by finite frames and deadline; on completion/Back/exit send only the bounded zero-intensity terminator; halt and release all GUI/storage/radio resources on exit or failure. Never send RF during build or tests.
- [x] **Build both SDK targets** by staging public controller sources and canonical radio core, strip builder paths/debug data in the same manner as existing public FAPs, and verify ARM/f7/FAP API metadata/import availability. Re-run portable tests. Inspect the screen layout against 128×64 dimensions.
- [x] **Commit** source, public build helper, verified FAPs and asset hashes after review. Mark physical behavior unverified until a user-driven device check.

### Task 3: Verified controller installation with automatic target assignment

**Files:** Modify `host/flipper_install.py` and `host/test_flipper_install.py`; create `host/controller_setup.py` and `host/test_controller_setup.py`.

**Interfaces:**
- Preserve existing `install_app(...)`, `_asset_bytes(...)`, `ASSETS` and `APP_PATH` behavior for USB Radio.
- Add private reusable verified-file transfer helper accepting an already identified `_Console`, fixed destination, bytes, progress, cancellation and folders. Keep installation-specific paths internal, never accept arbitrary GUI text as paths.
- Add `ControllerSetupResult(device: DeviceInfo, app_path: str, target_path: str)` and `install_controller(port, asset_dir, shocker_id: int, channel: int, *, progress=None, cancel=None, serial_factory=None) -> ControllerSetupResult`.
- Add `update_controller_target(port, shocker_id: int, channel: int, *, progress=None, cancel=None, serial_factory=None) -> ControllerSetupResult` and `target_bytes(shocker_id: int, channel: int) -> bytes`. Callers pass only selected numeric target fields, never the full profile.

- [x] **Write failing tests** through the existing fake serial/storage boundary: exact canonical target bytes and bounds; no private profile fields; API-specific controller selected; original USB Radio untouched; both app and target read back before success; existing identical app still gets target updated; target-only update verifies installed compatible controller; running app/unsupported API/cancelled input causes no writes.
- [x] **Add failure tests** for staging mismatch, backup mismatch, interrupted final app copy, interrupted target copy after app success, stale prior target, final target corruption, cancellation before/after commit starts, USB binary framing loss and cleanup names. On target failure the UI result must not report ready; reconnect/update remains available. Existing USB installer regressions must pass unchanged.
- [x] **Run RED** with `python -B -W error -m unittest discover -s host -p 'test_controller_setup.py' -v`.
- [x] **Implement** using one console ownership interval, require-idle before each final copy, verified unique staging/backup, bounded exact target serialization and fixed private-safe messages. Install the matching shared FAP first and target second; no launch, RF, firmware command or arbitrary identity write. Report partial application-success/target-failure explicitly; do not imply multi-file atomicity.
- [x] **Run GREEN** for controller setup and existing installer tests, with fake USB only. Check target payload contains only Filetype/Version/ShockerId/Channel and never emit raw serial responses or secrets on failures.
- [x] **Commit** the installation code and regression tests after review.

### Task 4: Desktop setup controls and bundled assets

**Files:** Modify `host/desktop_app.py`, `host/test_desktop_app.py`, `packaging/build_windows.py`, `packaging/installer.iss`; extend package resource audit allowlists only where necessary.

**Interfaces:** Consumes Task 3 functions using `profile.shocker_id`, `profile.channel`, selected installation console port, the existing job worker/progress/cancel queue and resource root. Retains the single normal/direct session slot.

- [x] **Write failing GUI tests** for missing profile/console selection, disabled actions during bridge/direct/copy, install passes exact selected ID/channel, changing selected profile changes the next install target, update does not reinstall FAP, success waits for target verification, partial failure keeps instructions actionable, and Exit during copy waits for cleanup. Demo mode must not enumerate/open USB, load profiles or write target files.
- [x] **Run RED** with the focused Tk unittest class and explicit fixture destruction/owner-thread garbage collection.
- [x] **Implement Set up devices controller section** showing the saved target, **Install standalone controller** (FAP plus automatic assignment) and **Update controller target**. Explain closing any Flipper app first, show final target on success, and direct users to Apps → Sub-GHz → PiShock Controller. Shared downloads remain generic; only device setup personalizes the target file.
- [x] **Add assets/runtime modules** to packaging and validate both controller assets in packaged startup self-test. Set final controller release version to 0.4.0 consistently; retain 0.3.2 direct-beep release artifacts if already built.
- [x] **Run GREEN** for complete Python suite with warnings as errors, Tk cleanup stress, both themes and minimum-size scrolling/layout. Neither test nor preview may touch hardware/profile data.
- [x] **Commit** GUI/package changes after review.

### Task 5: Documentation, package verification and local delivery

**Files:** Modify `README.md`, `docs/USER_GUIDE.md`, `docs/BUILD.md`, `CHANGELOG.md`; preserve `NOTICE.md` acknowledgments; save release artifacts/publishing notes outside the public checkout as existing releases do.

- [x] **Document** automatic selected-target assignment, target-only update, installation from the bridge, offline controls/arming/Back, pairing using a beep, SD configuration privacy, API requirements, cloud settings not applying offline, no automatic keep-awake, and independent USB Radio operation. Do not claim physical validation without evidence.
- [x] **Run whole-change review** against the approved spec, including install partial failures and input sequencing. Fix important findings with regression tests and rerun affected checks.
- [x] **Run final verification**: full Python warnings-as-errors suite, both strict native C tests, both SDK builds/import checks, source privacy audit, Windows package build/startup/bundle privacy audit. Confirm repository contains no real target configuration; release FAP hashes match packaged assets; preserve `cli-original`.
- [x] **Save versioned artifacts/checksums/build metadata** for 0.4.0 without replacing older release installers. Prepare local release notes and manual `git push -u origin main`/optional release-upload instructions. Do not push or publish.
- [x] **Offer user-driven device validation** after clean builds: install/controller startup/target display, unplug USB, physically arm and send one off-body beep, verify Back/relaunch disarm, then verify USB Radio still opens. Never automate vibration or shock. Record actual results or label hardware verification pending.
- [x] **Commit final documentation** and report local installer paths, tested behavior, limitations and manual publishing commands.

## Completion record

Implemented and reviewed locally on 2026-10-05. Software verification passed 292 Python tests, 132 repeated GUI cleanup checks, native core/encoder and simulated adapter checks, both SDK builds/import checks, actual asset hashes, and packaged startup/privacy checks. The final review found and resolved a stale transmission-error display that could hide physical re-arming. The Windows installer and portable ZIP were rebuilt with the corrected FAPs. Physical installation, button/display behavior, and receiver delivery remain unverified; the user guide provides the off-body first-beep procedure. Nothing was pushed or published.
