"""What a PDF editor that never opened was waiting for: who holds PDFium, and what every thread is doing."""
import sys
import threading
import traceback


def stall_report(ed) -> str:
    from corefold import pdftools
    r = ed.renderer
    lines = [f"pages {len(ed.pages)}, saved {ed.saved is not None}, renderer alive "
             f"{r is not None and r.thread.is_alive()}, renders {r and len(r.renders)}, layouts {r and r.layouts}, "
             f"PDFium {pdftools.PDFIUM!r}"]
    frames = sys._current_frames()
    for t in threading.enumerate():
        if t is threading.main_thread():
            continue
        stack = "".join(traceback.format_stack(frames.get(t.ident))[-4:]) if t.ident in frames else ""
        lines.append(f"--- {t.name} ({t.ident})\n{stack}")
    return "\n".join(lines)
