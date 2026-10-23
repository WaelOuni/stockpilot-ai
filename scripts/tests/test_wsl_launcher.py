import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts import wsl_launcher as w
from scripts import b2b_cold_preflight as p

class LauncherTests(unittest.TestCase):
    @contextmanager
    def available_host(self):
        # These argv/error unit tests must also run on native Linux CI, which
        # has no Windows System32 mount. Real cwd refusal tests stay unmocked.
        with patch.object(w.Path, 'is_dir', return_value=True), patch.object(w.os, 'access', return_value=True):
            yield

    def result(self, rc=0, out=b'/home/waelko\n', err=b''):
        return subprocess.CompletedProcess([],rc,out,err)

    def test_explicit_ubuntu_argv_and_host_cwd(self):
        with self.available_host(), patch.object(w.subprocess,'run',return_value=self.result()) as run:
            self.assertEqual(w.run_wsl('Ubuntu',['/usr/bin/pwd']),b'/home/waelko\n')
        self.assertEqual(run.call_args.args[0],[w.WSL,'--distribution','Ubuntu','--cd','/home/waelko','--exec','/usr/bin/pwd'])
        self.assertEqual(run.call_args.kwargs['cwd'],str(w.WINDOWS_CWD))
        self.assertIs(run.call_args.kwargs['shell'],False)

    def test_desktop_fixed_root(self):
        with self.available_host(), patch.object(w.subprocess,'run',return_value=self.result()) as run:
            w.run_wsl('docker-desktop',['df','-B1','/mnt/docker-desktop-disk'])
        self.assertEqual(run.call_args.args[0][1:6],['--distribution','docker-desktop','--cd','/','--exec'])

    def test_list_has_representable_host_cwd(self):
        with self.available_host(), patch.object(w.subprocess,'run',return_value=self.result()) as run:w.list_running()
        self.assertEqual(run.call_args.args[0],[w.WSL,'--list','--running','--quiet'])
        self.assertEqual(run.call_args.kwargs['cwd'],str(w.WINDOWS_CWD))

    def test_special_characters_remain_argv(self):
        args=['/usr/bin/printf','%s','spaces & ; $ ( ) " \\ é']
        with self.available_host(), patch.object(w.subprocess,'run',return_value=self.result()) as run:w.run_wsl('Ubuntu',args)
        self.assertEqual(run.call_args.args[0][6:],args)

    def test_host_cwd_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='wsl cwd spaces ') as temp, patch.object(w.subprocess,'run',return_value=self.result()) as run:
            w.run_wsl('Ubuntu',['/usr/bin/pwd'],host_cwd=temp)
            self.assertEqual(run.call_args.kwargs['cwd'],temp)

    def test_missing_host_cwd_does_not_launch(self):
        with patch.object(w.subprocess,'run') as run:
            with self.assertRaisesRegex(w.WslLaunchError,'host_cwd_unavailable'):
                w.run_wsl('Ubuntu',['/usr/bin/pwd'],host_cwd='/missing/stockpilot-launcher-test')
        run.assert_not_called()

    def test_inaccessible_host_cwd_does_not_launch(self):
        with patch.object(w.Path,'is_dir',return_value=True),patch.object(w.os,'access',return_value=False),patch.object(w.subprocess,'run') as run:
            with self.assertRaisesRegex(w.WslLaunchError,'host_cwd_unavailable'):w.run_wsl('Ubuntu',['/usr/bin/pwd'])
        run.assert_not_called()

    def test_nonzero_and_stderr_refuse_without_losing_raw_diagnostics(self):
        for rc,err,reason in [(4,b'', 'nonzero_exit'),(0,b'unexpected warning\n','unexpected_stderr'),(0,b'wsl: Failed to translate UNC\n','unexpected_stderr')]:
            with self.subTest(rc=rc,err=err),self.available_host(),patch.object(w.subprocess,'run',return_value=self.result(rc=rc,err=err)):
                with self.assertRaisesRegex(w.WslLaunchError,reason) as caught:w.run_wsl('Ubuntu',['/usr/bin/pwd'])
                self.assertEqual(caught.exception.result.stderr,err)

    def test_unavailable_or_timeout(self):
        for exc in [FileNotFoundError(),PermissionError(),subprocess.TimeoutExpired('wsl',1)]:
            with self.available_host(),patch.object(w.subprocess,'run',side_effect=exc):
                with self.assertRaisesRegex(w.WslLaunchError,'unavailable_or_timeout'):w.run_wsl('Ubuntu',['/usr/bin/pwd'])

    def test_unknown_distribution_cwd_and_arguments(self):
        with patch.object(w.subprocess,'run') as run:
            for dist,args,cwd in [('Other',['pwd'],None),('Ubuntu',['pwd'],'relative'),('Ubuntu',['pwd'],'/tmp/../x'),('Ubuntu','pwd',None),('Ubuntu',['a\0b'],None)]:
                with self.assertRaises(w.WslLaunchError):w.run_wsl(dist,args,linux_cwd=cwd)
        run.assert_not_called()

    def test_preflight_propagates_wsl_refusal(self):
        with patch.object(p,'run_wsl',side_effect=w.WslLaunchError('wsl_unexpected_stderr')):
            with self.assertRaisesRegex(p.Refused,'wsl_unexpected_stderr'):p.wsl_command('docker-desktop',['pwd'])

    def test_wmi_unavailable_or_invalid_still_refuses(self):
        with patch.object(p,'command',return_value=b'C:\\helper.js\n'):
            for result in [FileNotFoundError(),self.result(out=b'not JSON'),self.result(out=b'{"version":1,"result":"BLOCKED"}'),self.result(err=b'unknown')]:
                with self.subTest(result=type(result).__name__):
                    kwargs={'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
                    with patch.object(p.subprocess,'run',**kwargs) as run,self.assertRaises(p.Refused):p.windows(36*1024**3)
                    self.assertEqual(run.call_args.kwargs['cwd'],str(w.WINDOWS_CWD))

    def test_negative_wmi_or_wsl_preflight_never_reaches_docker(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);state=base/'b2b-demo';state.mkdir(mode=0o700)
            parent=base/'b2b-backups';parent.mkdir(mode=0o700)
            lock=base/'b2b-demo.lock';lock.write_bytes(b'');lock.chmod(0o600)
            fake=types.ModuleType('scripts.b2b_interactive')
            fake.InteractiveDemo=lambda:types.SimpleNamespace(state=state)
            for code in ['wmi_unavailable','wsl_unexpected_stderr']:
                evidence=base/code;evidence.mkdir(mode=0o700)
                with patch.dict('sys.modules',{'scripts.b2b_interactive':fake}),patch.object(p,'running_distributions',return_value=b'Ubuntu\ndocker-desktop'),patch.object(p,'linux_administration'),patch.object(p,'windows',side_effect=p.Refused(code)),patch.object(p,'command') as command:
                    with self.assertRaisesRegex(p.Refused,code):p.run(evidence,parent/'v1393-point-test-cold')
                    command.assert_not_called()

    def test_wsl_listing_failure_never_reaches_wmi_or_docker(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);state=base/'b2b-demo';state.mkdir(mode=0o700)
            parent=base/'b2b-backups';parent.mkdir(mode=0o700)
            lock=base/'b2b-demo.lock';lock.write_bytes(b'');lock.chmod(0o600)
            evidence=base/'evidence';evidence.mkdir(mode=0o700)
            fake=types.ModuleType('scripts.b2b_interactive')
            fake.InteractiveDemo=lambda:types.SimpleNamespace(state=state)
            with patch.dict('sys.modules',{'scripts.b2b_interactive':fake}),patch.object(p,'running_distributions',side_effect=p.Refused('wsl_unexpected_stderr')),patch.object(p,'windows') as windows,patch.object(p,'command') as command:
                with self.assertRaisesRegex(p.Refused,'wsl_unexpected_stderr'):p.run(evidence,parent/'v1393-point-test-cold')
                windows.assert_not_called();command.assert_not_called()

if __name__=='__main__':unittest.main()
