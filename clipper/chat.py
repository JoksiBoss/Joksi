"""Anonymous Twitch chat logger (justinfan) -> message timestamps for chat-velocity spikes."""
import random
import socket
import threading
import time

from .common import log


class ChatLogger(threading.Thread):
    def __init__(self, channel):
        super().__init__(daemon=True)
        self.channel = channel.lower().lstrip("#")
        self.times = []
        self.lock = threading.Lock()
        self.stop_flag = False
        self.connected = False

    def run(self):
        while not self.stop_flag:
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                nick = f"justinfan{random.randint(10000, 99999)}"
                s.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
                self.connected = True
                log(f"[chat] connected to #{self.channel}")
                buf = b""
                s.settimeout(330)
                while not self.stop_flag:
                    data = s.recv(65536)
                    if not data:
                        raise ConnectionError("closed")
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    now = time.time()
                    n = 0
                    for ln in lines:
                        if ln.startswith(b"PING"):
                            s.sendall(b"PONG" + ln[4:] + b"\r\n")
                        elif b" PRIVMSG #" in ln:
                            n += 1
                    if n:
                        with self.lock:
                            self.times.extend([now] * n)
            except Exception as e:  # reconnect
                self.connected = False
                log(f"[chat] {e!r}; reconnecting in 5s")
                time.sleep(5)

    def counts(self, t0, seconds, lag=6.0):
        """Messages per second for wall-clock window [t0, t0+seconds), shifted back by `lag` (chat reacts late)."""
        out = [0] * int(seconds)
        with self.lock:
            ts = list(self.times)
        for t in ts:
            i = int(t - lag - t0)
            if 0 <= i < len(out):
                out[i] += 1
        return out

    def total(self):
        with self.lock:
            return len(self.times)
