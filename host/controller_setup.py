"""Verified offline controller setup: fixed files and numeric target fields only."""
from dataclasses import dataclass
from pathlib import Path
from flipper_install import (DeviceInfo, InstallError, InstallCancelled, _Console,
    _check_cancel, _require_supported, _validate_asset_bytes, _verified_file)

APP_PATH = '/ext/apps/Sub-GHz/pishock_controller.fap'
TARGET_PATH = '/ext/apps_data/pishock_controller/target.conf'
CONTROLLER_ASSETS = {(87,1):'pishock_controller-official-1.4.3.fap',
                     (88,9):'pishock_controller-api-88.9.fap'}

@dataclass(frozen=True)
class ControllerSetupResult:
    device: DeviceInfo
    app_path: str
    target_path: str

def target_bytes(shocker_id: int, channel: int) -> bytes:
    if type(shocker_id) is not int or type(channel) is not int or not 1 <= shocker_id <= 65535 or not 0 <= channel <= 2:
        raise InstallError('Choose a valid controller target ID and channel before setup.')
    return f'Filetype: PiShock Controller Target\nVersion: 1\nShockerId: {shocker_id}\nChannel: {channel}\n'.encode('ascii')

def _asset_bytes(asset_dir, api):
    try:
        return _validate_asset_bytes((Path(asset_dir)/CONTROLLER_ASSETS[api]).read_bytes(),api, expected_name="PiShock Controller")
    except (KeyError,OSError):
        raise InstallError('The bundled controller application is missing or incompatible. Reinstall the desktop application.') from None

def _setup(port, asset_dir, shocker_id, channel, *, progress=None, cancel=None, serial_factory=None):
    target=target_bytes(shocker_id,channel)
    _check_cancel(cancel)
    report=progress or (lambda percent,message:None)
    console=_Console(port,serial_factory)
    app_verified=False
    try:
        report(0,'Checking controller compatibility…')
        device=console.identify()
        _require_supported(device)
        api=(device.api_major,device.api_minor)
        console.require_idle()
        if asset_dir is None:
            if not console.exists(APP_PATH):
                raise InstallError('Install the standalone controller before updating its target.')
            _validate_asset_bytes(console.read_file(APP_PATH),api, expected_name="PiShock Controller")
        else:
            app=_asset_bytes(asset_dir,api)
            _verified_file(console,APP_PATH,app,progress=lambda p,m:report(p//2,'Installing controller application…'),cancel=cancel,
                           folders=('/ext/apps','/ext/apps/Sub-GHz'))
        app_verified=True
        _verified_file(console,TARGET_PATH,target,progress=lambda p,m:report(50+p//2,'Writing and verifying controller target…'),cancel=cancel,
                       folders=('/ext/apps_data','/ext/apps_data/pishock_controller'))
        report(100,'Controller application and selected target verified. Check the displayed target before arming.')
        return ControllerSetupResult(device,APP_PATH,TARGET_PATH)
    except InstallError as error:
        if app_verified:
            kind=InstallCancelled if isinstance(error,InstallCancelled) else InstallError
            raise kind('Controller application is verified, but the selected target was not verified. A previous target may remain. Reconnect USB and repeat setup or update the target. Repeat setup before operating the controller.') from None
        raise
    finally:
        console.close()

def install_controller(port, asset_dir, shocker_id: int, channel: int, *, progress=None,cancel=None,serial_factory=None):
    return _setup(port,asset_dir,shocker_id,channel,progress=progress,cancel=cancel,serial_factory=serial_factory)

def update_controller_target(port,shocker_id: int,channel: int,*,progress=None,cancel=None,serial_factory=None):
    return _setup(port,None,shocker_id,channel,progress=progress,cancel=cancel,serial_factory=serial_factory)
