#!/usr/bin/python3
"""Retain real simulator exits and output, using unbuffered vvp diagnostics."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def main():
    tool = Path(sys.argv[0]).name
    if tool not in ("vvp", "iverilog"):
        raise SystemExit("unsupported verification wrapper")
    command = ["/usr/bin/" + tool]
    if tool == "vvp":
        command.append("-i")  # Flush startup $display lines before cocotb logs.
    command.extend(sys.argv[1:])
    audit = Path(os.environ["CHIPFLOW_PROCESS_AUDIT"])
    logs = audit.with_suffix(".logs")
    logs.mkdir(parents=True, exist_ok=True)
    identifier = tool + "-" + uuid.uuid4().hex
    started = time.time()
    result = subprocess.run(command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    row = {"command": command, "cwd": os.getcwd(), "returncode": result.returncode,
           "started_unix": started, "ended_unix": time.time()}
    for channel in ("stdout", "stderr"):
        data = getattr(result, channel)
        path = logs / (identifier + "." + channel + ".log")
        path.write_bytes(data)
        row[channel + "_log"] = str(path)
        row[channel + "_sha256"] = hashlib.sha256(data).hexdigest()
        getattr(sys, channel).buffer.write(data)
        getattr(sys, channel).buffer.flush()
    with audit.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(row) + "\n")
        stream.flush()
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
