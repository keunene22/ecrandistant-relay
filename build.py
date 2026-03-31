"""
Build standalone .exe files using PyInstaller.

Usage:
    python build.py

Produces:
    dist/ecrandistant_host.exe
    dist/ecrandistant_client.exe

Requires:  pip install pyinstaller
"""
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))

COMMON = [
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    f'--add-data=shared{os.pathsep}shared',
]

TARGETS = [
    {
        'script':  'main_host.py',
        'name':    'ecrandistant_host',
        'console': True,
        'extra':   [f'--add-data=host{os.pathsep}host'],
    },
    {
        'script':  'main_client.py',
        'name':    'ecrandistant_client',
        'console': False,
        'extra':   [f'--add-data=client{os.pathsep}client'],
    },
]

HIDDEN = [
    '--hidden-import=mss',
    '--hidden-import=PIL',
    '--hidden-import=pynput',
    '--hidden-import=websockets',
    '--hidden-import=sounddevice',
    '--hidden-import=numpy',
    '--hidden-import=pyperclip',
    '--hidden-import=PyQt6',
    '--hidden-import=cryptography',
]


def build(target: dict):
    cmd = COMMON + HIDDEN + target['extra'] + [
        '--onefile',
        '--name', target['name'],
    ]
    if not target['console']:
        cmd.append('--windowed')
    cmd.append(target['script'])

    print(f'\n>>> Building {target["name"]}…')
    subprocess.run(cmd, cwd=BASE, check=True)
    print(f'>>> Done: dist/{target["name"]}.exe')


if __name__ == '__main__':
    os.chdir(BASE)
    for t in TARGETS:
        build(t)
    print('\nAll builds complete! Check the dist/ folder.')
