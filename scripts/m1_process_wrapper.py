#!/usr/bin/python3
"""Record actual simulator process exits without changing captured S8 sources."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

tool = Path(sys.argv[0]).name
if tool not in ('vvp', 'iverilog'):
    raise SystemExit('unsupported audit wrapper')
command = ['/usr/bin/' + tool, *sys.argv[1:]]
started = time.time()
result = subprocess.run(command, check=False)
row = dict(command=command, cwd=os.getcwd(), returncode=result.returncode, started_unix=started, ended_unix=time.time())
with open(os.environ['CHIPFLOW_PROCESS_AUDIT'], 'a', encoding='utf-8') as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    stream.flush()
raise SystemExit(result.returncode)
