import sys
import threading

from dealhunter import instance, logs, window
from dealhunter.engine import Engine
from dealhunter.notify import Notifier
from dealhunter.server import Server
from dealhunter.tray import Tray


def main():
    logs.setup()
    holder = {"proc": None}
    quit_evt = threading.Event()

    def show():
        srv = holder.get("srv")
        proc = holder["proc"]
        if srv and (proc is None or proc.poll() is not None):
            holder["proc"] = window.open_window(srv.url)

    if not instance.acquire(show):
        return
    notifier = Notifier()
    engine = Engine(notifier)
    srv = Server(engine, notifier, on_quit=quit_evt.set)
    srv.start()
    holder["srv"] = srv
    tray = Tray(show, engine.check_now, quit_evt.set)
    has_tray = tray.start()
    notifier.toast = tray.notify if has_tray else None
    threading.Thread(target=engine.loop, daemon=True).start()
    if not ("--minimized" in sys.argv and has_tray):
        show()
    if has_tray or holder["proc"] is None:
        quit_evt.wait()
    else:  # من غير tray: لما النافذة تتقفل البرنامج يقفل
        holder["proc"].wait()
    engine.stop.set()
    engine.wake.set()
    tray.stop()
    srv.stop()


if __name__ == "__main__":
    main()
