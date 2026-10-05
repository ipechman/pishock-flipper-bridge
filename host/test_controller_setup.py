import tempfile
import threading
import unittest
from pathlib import Path
import controller_setup as setup
import flipper_install as install
from test_flipper_install import FakeFlipper, make_fap as make_usb_fap
import struct

def make_fap(api=(87,1), name=b"PiShock Controller", truncated=False):
    data=bytearray(make_usb_fap(api))
    start,size=struct.unpack_from("<II",data,148)
    extension=bytes(6)+name.ljust(32,b"\0")+bytes(33)
    if truncated: extension=extension[:10]
    data[start+size:start+size]=extension
    struct.pack_into("<I",data,152,size+len(extension))
    return bytes(data)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.assets = Path(self.tmp.name)
        for api, name in setup.CONTROLLER_ASSETS.items():
            (self.assets / name).write_bytes(make_fap(api))
    def run_setup(self, fake, **kw):
        return setup.install_controller('COM5', self.assets, 123, 2, serial_factory=fake.factory, **kw)
    def test_target_canonical_and_strict(self):
        self.assertEqual(setup.target_bytes(123, 2), b'Filetype: PiShock Controller Target\nVersion: 1\nShockerId: 123\nChannel: 2\n')
        for sid, channel in ((True,0),(1,False),(0,0),(65536,0),(1,-1),(1,3),('1',0),(1,0.0)):
            with self.subTest(sid=sid, channel=channel), self.assertRaises(install.InstallError):
                setup.target_bytes(sid, channel)
    def test_both_verified_and_radio_untouched(self):
        for api in install.ASSETS:
            fake = FakeFlipper(api=api, files={install.APP_PATH:b'keep'})
            result = self.run_setup(fake)
            self.assertEqual(fake.files, {install.APP_PATH:b'keep',setup.APP_PATH:make_fap(api),setup.TARGET_PATH:setup.target_bytes(123,2)})
            self.assertEqual(result.target_path, setup.TARGET_PATH)
            self.assertTrue(fake.closed)
            for path in (setup.APP_PATH,setup.TARGET_PATH):
                self.assertTrue(any(c.startswith('storage read_chunks "'+path+'"') for c in fake.commands))
    def test_identical_app_still_updates_target(self):
        fake = FakeFlipper(files={setup.APP_PATH:make_fap(),setup.TARGET_PATH:setup.target_bytes(2,0)})
        self.run_setup(fake)
        self.assertEqual(fake.write_count,1)
        self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(123,2))
    def test_target_update_requires_compatible_app(self):
        for app in (None,b'bad',make_fap((88,9)),make_usb_fap(),make_fap(name=b"PiShock USB Radio"),make_fap(truncated=True)):
            fake=FakeFlipper(files={} if app is None else {setup.APP_PATH:app})
            with self.assertRaises(install.InstallError):
                setup.update_controller_target('COM5',123,2,serial_factory=fake.factory)
            self.assertEqual(fake.write_count,0)
        fake=FakeFlipper(files={setup.APP_PATH:make_fap()})
        setup.update_controller_target('COM5',123,2,serial_factory=fake.factory)
        self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(123,2))
    def test_preconditions_no_writes(self):
        for fake in (FakeFlipper(api=(90,0)),FakeFlipper()):
            if fake.api==(87,1): fake.app_running=True
            with self.assertRaises(install.InstallError): self.run_setup(fake)
            self.assertEqual(fake.write_count,0)
        fake=FakeFlipper()
        with self.assertRaises(install.InstallCancelled): self.run_setup(fake,cancel=lambda:True)
        self.assertEqual(fake.commands,[])
    def test_partial_target_failure_warns_even_with_previous_target(self):
        fake=FakeFlipper(files={setup.TARGET_PATH:setup.target_bytes(2,0)})
        fake.fail_chunk=(len(make_fap())+install.CHUNK_SIZE-1)//install.CHUNK_SIZE+1
        with self.assertRaisesRegex(install.InstallError,'application.*verified.*target.*Repeat setup before operating'):
            self.run_setup(fake)
        self.assertEqual(fake.files[setup.APP_PATH],make_fap())
        self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(2,0))
    def test_binary_loss_never_cleanup_payload(self):
        fake=FakeFlipper(); fake.break_binary=True
        with self.assertRaises(install.InstallError): self.run_setup(fake)
        self.assertTrue(fake.commands[-1].startswith('storage write_chunk'))
    def test_corrupt_staging_no_commit(self):
        fake=FakeFlipper(); fake.corrupt_staging=True
        with self.assertRaises(install.InstallError): self.run_setup(fake)
        self.assertFalse(any('rename' in c for c in fake.commands))

class FaultFlipper(FakeFlipper):
    def __init__(self, *, fault, **kwargs):
        super().__init__(**kwargs)
        self.fault = fault
    def _command(self, command):
        import shlex
        parts = shlex.split(command)
        if parts[:2] == ['storage','read_chunks'] and ((self.fault == 'backup' and parts[2].endswith('.backup')) or
                (self.fault == 'target_final' and parts[2] == setup.TARGET_PATH)):
            old = self.files[parts[2]]
            self.files[parts[2]] = old[:-1]+bytes([old[-1]^1])
            super()._command(command)
            self.files[parts[2]] = old
            return
        if parts[:2] == ['storage','rename'] and ((self.fault == 'app_copy' and parts[3] == setup.APP_PATH) or
                (self.fault == 'target_copy' and parts[3] == setup.TARGET_PATH)):
            self.break_rename=True
        super()._command(command)
        self.break_rename=False

class ControllerFailureTests(ControllerTests):
    def test_backup_mismatch_keeps_previous_app(self):
        fake=FaultFlipper(fault='backup',files={setup.APP_PATH:b'old'})
        with self.assertRaisesRegex(install.InstallError,'backup'):
            self.run_setup(fake)
        self.assertEqual(fake.files,{setup.APP_PATH:b'old'})
    def test_interrupted_final_app_restores_previous(self):
        fake=FaultFlipper(fault='app_copy',files={setup.APP_PATH:b'old'})
        with self.assertRaisesRegex(install.InstallError,'restored and verified'):
            self.run_setup(fake)
        self.assertEqual(fake.files[setup.APP_PATH],b'old')
        self.assertNotIn(setup.TARGET_PATH,fake.files)
    def test_target_final_faults_never_claim_ready(self):
        for fault in ('target_copy','target_final'):
            fake=FaultFlipper(fault=fault,files={setup.TARGET_PATH:setup.target_bytes(2,0)})
            messages=[]
            with self.assertRaisesRegex(install.InstallError,'Repeat setup before operating'):
                self.run_setup(fake,progress=lambda p,m:messages.append(m))
            self.assertEqual(fake.files[setup.APP_PATH],make_fap())
            self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(2,0))
            self.assertFalse(any('selected target verified' in m for m in messages))
    def test_cancellation_before_target_commit_retains_prior_target(self):
        fake=FakeFlipper(files={setup.APP_PATH:make_fap(),setup.TARGET_PATH:setup.target_bytes(2,0)})
        cancel=threading.Event()
        def progress(percent,message):
            if percent==85: cancel.set()
        with self.assertRaisesRegex(install.InstallCancelled,'Repeat setup before operating'):
            self.run_setup(fake,cancel=cancel,progress=progress)
        self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(2,0))
        self.assertFalse(any('rename' in c for c in fake.commands))
    def test_cancellation_after_target_commit_completes_verified_file(self):
        fake=FakeFlipper(files={setup.APP_PATH:make_fap()})
        cancel=threading.Event()
        def progress(percent,message):
            if percent==92: cancel.set()
        self.run_setup(fake,cancel=cancel,progress=progress)
        self.assertEqual(fake.files[setup.TARGET_PATH],setup.target_bytes(123,2))
        self.assertTrue(cancel.is_set())
    def test_cleanup_names_are_unique_and_unrelated_files_kept(self):
        other=setup.TARGET_PATH+'.other.upload'
        fake=FakeFlipper(files={other:b'keep'})
        self.run_setup(fake)
        self.assertEqual(fake.files[other],b'keep')
        removes=[c for c in fake.commands if c.startswith('storage remove')]
        self.assertFalse(any(other in c for c in removes))

if __name__=='__main__': unittest.main()
