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
    try:
        import win32com.client                       # pywin32
    except ImportError:
        raise SystemExit('pywin32 is needed to write a .lnk (pip install pywin32)')
    shell = win32com.client.Dispatch('WScript.Shell')
    lnk = shell.CreateShortCut(str(HERE / 'DOI Downloader.lnk'))
    lnk.TargetPath = str(pythonw)
    lnk.Arguments = f'"{HERE / "app.pyw"}"'
    lnk.WorkingDirectory = str(HERE)
    lnk.Description = 'DOI Downloader'
    lnk.Save()
    print(f'Shortcut written: {HERE / "DOI Downloader.lnk"} -> {pythonw} app.pyw')


if __name__ == '__main__':
    main()
