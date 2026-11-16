"""Own the disposable preview lifetime so each browser run gets fresh data."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

root=Path(__file__).resolve().parent.parent
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',58120))==0:
        raise SystemExit('Port 58120 is already occupied; refusing to test an existing service')
process=subprocess.Popen([sys.executable,'-B','tests/v20_preview.py'],cwd=root)
try:
    for attempt in range(1200):
        if process.poll() is not None:raise RuntimeError('Preview exited before ready')
        try:
            with urllib.request.urlopen('http://127.0.0.1:58120/health',timeout=1) as response:
                if response.status==200:break
        except OSError:time.sleep(.1)
    else:raise RuntimeError('Preview startup timed out')
    subprocess.run(['node','tests/v20_browser.mjs'],cwd=root,check=True)
finally:
    process.send_signal(signal.SIGINT)
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill();process.wait()
