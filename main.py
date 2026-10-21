"""Entry point for Satsuma (used by PyInstaller and for running from source)."""
import sys
import time

if sys.platform != "win32":
    sys.exit("Satsuma only runs on Windows.")

# The elevated helper used by the Apps page (removing leftovers in Program Files, restore points).
if any(a in ("--cleanup", "--restore", "--restore-point") for a in sys.argv[1:]):
    from satsuma.uninstall import helper_main  # noqa: E402
    sys.exit(helper_main(sys.argv[1:]))

# Fast path first: if Satsuma is already running, hand it this request and quit before loading
# the heavy parts (Qt). This keeps right-clicking many files quick and light.
from satsuma import ipc  # noqa: E402

if ipc.forward(sys.argv[1:]):
    sys.exit(0)

if "--remove-shell-menu" in sys.argv:  # used by the uninstaller
    from satsuma import shellmenu  # noqa: E402
    shellmenu.remove()
    sys.exit(0)

from satsuma import winutil  # noqa: E402

if not winutil.create_mutex():
    # Another copy is just starting (e.g. several files right-clicked at once) or just quitting
    # (a restart): wait for it, then hand over the request or take over.
    message = ipc.message_for(sys.argv[1:])
    for _ in range(60):
        time.sleep(0.2)
        if message is not None and ipc.send(message, wait_s=0.3):
            sys.exit(0)
        if winutil.create_mutex():
            break
    else:
        sys.exit(0)

from satsuma.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
