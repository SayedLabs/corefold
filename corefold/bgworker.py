"""The background remover's helper process.

The AI model and the libraries that run it take a few hundred MB. Running them in their own small
process means all of that is handed back to Windows the moment the job is done, instead of staying
with Corefold while it waits in the tray.

Corefold sends a 320×320 picture (raw RGB bytes) over a private pipe; the helper answers with a
320×320 mask (one byte per pixel). Only bytes travel, never Python objects, and the pipe only talks
to whoever knows the one-time key Corefold handed to this helper."""
from __future__ import annotations

import os
import sys

SIDE = 320
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


def mask_from_rgb(sess, data: bytes) -> bytes:
    """One 320×320 RGB picture in, its 320×320 mask out (the U²-Net recipe)."""
    import numpy as np
    x = np.frombuffer(data, dtype=np.uint8).reshape(SIDE, SIDE, 3).astype(np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    pred = (pred - pred.min()) / max(float(pred.max() - pred.min()), 1e-6)
    return (pred * 255).astype(np.uint8).tobytes()


def open_model(model: str):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])


def main(argv: list[str]) -> int:
    try:
        i = argv.index("--bg-worker")
        address, model = argv[i + 1], argv[i + 2]
    except (ValueError, IndexError):
        return 2
    key = bytes.fromhex(os.environ.pop("COREFOLD_WORKER_KEY", "") or "")
    if len(key) < 16 or not address.startswith("\\\\.\\pipe\\corefold-"):
        return 2
    from multiprocessing.connection import Listener
    sess = open_model(model)
    with Listener(address, "AF_PIPE", authkey=key) as listener:
        with listener.accept() as conn:
            while True:
                try:
                    data = conn.recv_bytes(maxlength=SIDE * SIDE * 3)
                except (EOFError, OSError):  # Corefold is done with us (or gone)
                    return 0
                if len(data) != SIDE * SIDE * 3:
                    return 3
                conn.send_bytes(mask_from_rgb(sess, data))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
