"""نسخة واحدة بس شغالة: لو فتحته تاني بيظهّر النافذة الموجودة."""
import socket
import threading

PORT = 47653


def acquire(on_show):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", PORT))
        s.listen(2)
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return False

    def loop():
        while True:
            try:
                conn, _ = s.accept()
                conn.close()
                on_show()
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()
    return True
