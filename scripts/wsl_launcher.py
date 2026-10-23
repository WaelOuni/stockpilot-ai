"""WSL argv launcher with explicit host and guest working directories."""
import os
from pathlib import Path, PurePosixPath
import subprocess

WSL = r'C:\Windows\System32\wsl.exe' if os.name == 'nt' else '/mnt/c/Windows/System32/wsl.exe'
WINDOWS_CWD = Path(r'C:\Windows\System32' if os.name == 'nt' else '/mnt/c/Windows/System32')
GUEST_CWD = {'Ubuntu': '/home/waelko', 'docker-desktop': '/'}

class WslLaunchError(RuntimeError):
    def __init__(self, reason, result=None):
        super().__init__(reason)
        self.reason = reason
        self.result = result  # Keep raw diagnostics available without printing command arguments/secrets.

def _run(argv, host_cwd, timeout):
    cwd = Path(host_cwd)
    try:
        if not cwd.is_absolute() or not cwd.is_dir() or not os.access(cwd, os.R_OK | os.X_OK):
            raise WslLaunchError('wsl_host_cwd_unavailable')
        result = subprocess.run(argv, cwd=str(cwd), capture_output=True, timeout=timeout, shell=False)
    except (OSError, subprocess.SubprocessError):
        raise WslLaunchError('wsl_unavailable_or_timeout') from None
    if result.returncode != 0:
        raise WslLaunchError('wsl_nonzero_exit', result)
    if result.stderr:
        raise WslLaunchError('wsl_unexpected_stderr', result)
    return result.stdout

def run_wsl(distribution, arguments, *, linux_cwd=None, host_cwd=WINDOWS_CWD, executable=WSL, timeout=60):
    if distribution not in GUEST_CWD:
        raise WslLaunchError('wsl_distribution_not_approved')
    cwd = GUEST_CWD[distribution] if linux_cwd is None else linux_cwd
    if not isinstance(cwd, str) or not PurePosixPath(cwd).is_absolute() or '..' in PurePosixPath(cwd).parts or '\x00' in cwd:
        raise WslLaunchError('wsl_invalid_guest_cwd')
    if not isinstance(arguments, (tuple, list)) or not arguments or not all(isinstance(x, str) and '\x00' not in x for x in arguments):
        raise WslLaunchError('wsl_invalid_arguments')
    return _run([str(executable), '--distribution', distribution, '--cd', cwd, '--exec', *arguments], host_cwd, timeout)

def list_running(*, host_cwd=WINDOWS_CWD, executable=WSL, timeout=60):
    # Listing does not execute a Linux process; --cd does not apply, but host cwd still does.
    return _run([str(executable), '--list', '--running', '--quiet'], host_cwd, timeout)

def version(*, host_cwd=WINDOWS_CWD, executable=WSL, timeout=60):
    return _run([str(executable), '--version'], host_cwd, timeout)
