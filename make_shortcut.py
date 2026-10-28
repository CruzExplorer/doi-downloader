"""Make 'DOI Downloader.lnk' next to app.pyw, pointing at this Python's pythonw.exe
(no console window). Run it once with the Python that has PyMuPDF installed:
    C:\\SLSU-Python\\Scripts\\python.exe make_shortcut.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.exists():
        raise SystemExit(f'pythonw.exe not found next to {sys.executable}')
    lnk_path = HERE / 'DOI Downloader.lnk'
    app = HERE / 'app.pyw'
    try:
        import win32com.client                       # pywin32, if installed
        shell = win32com.client.Dispatch('WScript.Shell')
        lnk = shell.CreateShortCut(str(lnk_path))
        lnk.TargetPath = str(pythonw)
        lnk.Arguments = f'"{app}"'
        lnk.WorkingDirectory = str(HERE)
        lnk.Description = 'DOI Downloader'
        lnk.Save()
        how = 'pywin32'
    except ImportError:                              # no pywin32: Windows PowerShell can do it
        import subprocess
        script = (f"$s = New-Object -ComObject WScript.Shell; $l = $s.CreateShortcut('{lnk_path}'); "
                  f"$l.TargetPath = '{pythonw}'; $l.Arguments = '\"{app}\"'; "
                  f"$l.WorkingDirectory = '{HERE}'; $l.Description = 'DOI Downloader'; $l.Save()")
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit('PowerShell could not write the shortcut:\n' + r.stderr)
        how = 'PowerShell'
    print(f'Shortcut written ({how}): {lnk_path} -> {pythonw} "{app}"')


if __name__ == '__main__':
    main()
