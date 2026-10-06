"""A stand-in for convert_host.py speaking the same protocol: "conversion" halves the samples
and doubles the rate; model "broken" fails; op "exit" ends the process mid-session."""

import json
import sys

import numpy as np

stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
while line := stdin.readline():
    request = json.loads(line)
    frames = int(request.get("frames", 0))
    audio = np.frombuffer(stdin.read(frames * 4), "<f4")
    if request["op"] == "exit":
        sys.exit(3)
    if request.get("model") == "broken":
        answer, out = {"ok": False, "error": "RuntimeError: модель сломана"}, None
    elif request["op"] == "ping":
        answer, out = {"ok": True, "device": "Fake GPU", "models": sys.argv[1]}, None
    elif request["op"] == "load":
        answer, out = {"ok": True}, None
    else:
        out = (audio * 0.5).astype("<f4")
        answer = {"ok": True, "rate": int(request["rate"]) * 2, "frames": len(out)}
    stdout.write(json.dumps(answer).encode() + b"\n")
    if out is not None:
        stdout.write(out.tobytes())
    stdout.flush()
