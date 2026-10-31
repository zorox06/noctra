"""One local launcher, no global Python packages or account credentials."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def ready(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def main():
    python = ROOT / '.venv' / 'Scripts' / 'python.exe' if os.name == 'nt' else ROOT / '.venv' / 'bin' / 'python'
    node = shutil.which('node')
    if not python.exists() or not node or not (ROOT / 'node_modules').exists():
        raise SystemExit('Dependencies missing. Run Setup-Noctra.cmd first (Windows).')
    if not (ROOT / '.next' / 'BUILD_ID').exists():
        raise SystemExit('Production build missing. Run npm run build in this folder first.')
    if ready('http://127.0.0.1:3000/api/health'):
        with urllib.request.urlopen('http://127.0.0.1:3000/api/health', timeout=3) as response:
            if json.load(response).get('app') == 'quantpad':
                if '--no-browser' not in sys.argv:
                    webbrowser.open('http://127.0.0.1:3000')
                print('Noctra is already running at http://127.0.0.1:3000')
                return
        raise SystemExit('Port 3000 is occupied by another application.')
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        if ready('http://127.0.0.1:8000/api/health'):
            with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3) as response:
                if json.load(response).get('app') != 'quantpad':
                    raise SystemExit('Port 8000 is occupied by another application.')
        else:
            processes.append(subprocess.Popen([str(python), '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'], cwd=ROOT, creationflags=flags))
        processes.append(subprocess.Popen([node, str(ROOT / 'node_modules' / 'next' / 'dist' / 'bin' / 'next'), 'start', '--hostname', '127.0.0.1', '--port', '3000'], cwd=ROOT, creationflags=flags))
        for _ in range(120):
            if any(p.poll() is not None for p in processes):
                raise SystemExit('A Noctra service stopped. Check the output above.')
            if ready('http://127.0.0.1:3000/api/health'):
                break
            time.sleep(.5)
        else:
            raise SystemExit('Noctra did not become ready in time.')
        if '--no-browser' not in sys.argv:
            webbrowser.open('http://127.0.0.1:3000')
        print('Noctra is running at http://127.0.0.1:3000')
        print('Keep this launcher open. Press Ctrl+C to stop the services started by it.')
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nStopping Noctra…')
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == '__main__':
    main()
