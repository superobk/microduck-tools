#!/usr/bin/env python3
"""Create a desktop-only isolated environment. No serial or SSH access."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import venv

WHEEL='pyserial-3.5-py2.py3-none-any.whl'
SHA='c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0'

def setup(root, wheelhouse):
    root=Path(root);wheel=Path(wheelhouse)/WHEEL
    if sys.version_info<(3,11):raise ValueError('需要Python 3.11或更高版本')
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=SHA:raise ValueError('离线依赖哈希不匹配')
    env=root/'.venv-workbench'
    venv.EnvBuilder(with_pip=True).create(env)
    python=env/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    subprocess.run([str(python),'-I','-m','pip','install','--no-index','--no-deps',str(wheel)],check=True)
    return python

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wheelhouse',type=Path)
    a=p.parse_args();root=Path(__file__).resolve().parents[1]
    python=setup(root,a.wheelhouse or root/'wheels')
    print('准备完成。启动 workbench.sh / workbench.cmd；默认演练不访问硬件。')
