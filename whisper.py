#!/usr/bin/env python3
"""
WHISPERLAN 🤫 — terminal whispers for your LAN
One file. Zero deps. Same WiFi. No server. No cloud.

  Run it like a real command:
    whisperlan --name ali          (short alias: whisper --name ali)
    whisperlan --name "ali" --port 54545
    whisperlan --help

  How it works:
  - UDP broadcast on port 54545 for discovery + the group room
  - TLS-encrypted TCP (port+1) for private 1-on-1s — unicast, nobody else gets them
  - Every peer shouts HELLO + HEARTBEAT, everyone else tracks who's online

  Commands inside:
    /p @bob hello      open encrypted private chat 🔒
    /room              back to the LAN room
    /dm @bob hello     one private msg, no view switch
    /name newname      rename (/nick works too)
    /me dances         action message
    /shrug /flip /unflip /party
    /online            who is here
    /clear             wipe current view
    /panic             NUKE all local history (asks first) 💥
    /help              this
    /quit              slip away quietly

  Tips:
  - All machines must be on the same WiFi/LAN, same --port.
  - macOS firewall may ask once — allow it.
  - Run two copies on one Mac to test (ports auto-bump).
"""
import argparse
import curses
import datetime
import getpass
import json
import os
import queue
import random
import socket
import ssl
import subprocess
import textwrap
import threading
import time
import uuid

VERSION = "1.3.0"
REPO = os.environ.get("WHISPER_REPO", "Tarraf2020/whisperlan")
UPDATE_URL = f"https://raw.githubusercontent.com/{REPO}/main/VERSION"
PORT_DEFAULT = 54545
LOG_PATH = os.path.expanduser("~/.whisper.log")
BROADCAST = "255.255.255.255"
HEARTBEAT_EVERY = 5
PEER_TIMEOUT = 15
TYPING_TIMEOUT = 4

BANNER = [
    "  _      ___    ________________ ___  _______ ",
    " | | /| / / |  / /  _/ __/ __/ _ \\/ __/ _ \\",
    " | |/ |/ /| | / // /_\\ \\_\\ \\/ __/ _// , _/",
    " |__/|__/ |_|/_/___/___/___/_/ /___/_/|_|   ",
    "        🤫 shhh… you're on the LAN now      ",
]

FUN = {
    "/shrug": "¯\\_(ツ)_/¯",
    "/flip": "(╯°□°）╯︵ ┻━┻",
    "/unflip": "┬─┬ ノ( ゜-゜ノ)",
    "/party": "♪┌(・。・)┘♪  ┪(･_･)┪  └(・。・)┐♪",
}

COLORS = [  # curses color pairs assigned per-user by hash
    curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_MAGENTA,
    curses.COLOR_YELLOW, curses.COLOR_BLUE, curses.COLOR_RED,
]

# ---------------------------------------------------------------- net ---

class Net:
    """UDP broadcast for the room + TLS-encrypted TCP for private 1-on-1s."""
    def __init__(self, nick, port, pport=None):
        self.nick = nick
        self.port = port
        self.pport = pport or (port + 1)  # private TCP port (auto-bumped if busy)
        self.uid = uuid.uuid4().hex[:8]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:  # needed on macOS to run 2 copies on same machine for testing
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(0.5)
        self.seen_ids = set()
        self.inbox = queue.Queue()       # room (UDP broadcast)
        self.priv_inbox = queue.Queue()  # private (TCP direct, TLS)
        self.running = True
        self.local_ip = self._local_ip()
        self.tls = self._ensure_cert()
        self._start_priv_server()

    def _local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    # -- TLS cert (self-signed, generated once via system openssl) --
    def _ensure_cert(self):
        home = os.path.expanduser("~")
        cert = os.path.join(home, ".whisper-cert.pem")
        key = os.path.join(home, ".whisper-key.pem")
        if not (os.path.exists(cert) and os.path.exists(key)):
            try:
                subprocess.run(
                    ["openssl", "req", "-x509", "-newkey", "rsa:2048",
                     "-keyout", key, "-out", cert, "-days", "825",
                     "-nodes", "-subj", "/CN=whisper-lan"],
                    check=True, capture_output=True, timeout=30)
                os.chmod(key, 0o600)
            except Exception:
                return False  # no openssl? fall back to plain TCP (still unicast-private)
        return os.path.exists(cert) and os.path.exists(key)

    def _server_ctx(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(os.path.expanduser("~/.whisper-cert.pem"),
                                os.path.expanduser("~/.whisper-key.pem"))
        except Exception:
            return None
        return ctx

    def _client_ctx(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # TOFU on trusted LAN: encrypted, not authenticated
        return ctx

    def _start_priv_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # find a free port (so 2 copies on one Mac both work)
        bound = False
        for cand in range(self.pport, self.pport + 20):
            try:
                srv.bind(("0.0.0.0", cand))
                self.pport = cand
                bound = True
                break
            except OSError:
                continue
        if not bound:
            srv.close()
            raise OSError(f"no free private port in {self.pport}..{self.pport + 19}")
        srv.listen(5)
        srv.settimeout(0.5)
        self._priv_srv = srv
        ctx = self._server_ctx() if self.tls else None

        def serve():
            while self.running:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._handle_priv_conn,
                                 args=(conn, ctx), daemon=True).start()
        threading.Thread(target=serve, daemon=True).start()

    def _handle_priv_conn(self, conn, ctx):
        try:
            if ctx:
                conn = ctx.wrap_socket(conn, server_side=True)
            conn.settimeout(5)
            buf = b""
            while self.running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        pkt = json.loads(line.decode())
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if pkt.get("uid") == self.uid:
                        continue
                    self.priv_inbox.put(pkt)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try: conn.close()
            except OSError: pass

    def send_private(self, ip, pport, pkt: dict) -> bool:
        """Unicast one packet over (TLS-)TCP. Returns True if delivered."""
        pkt.setdefault("v", 1)
        pkt.setdefault("id", uuid.uuid4().hex[:8])
        pkt.setdefault("from", self.nick)
        pkt.setdefault("uid", self.uid)
        pkt.setdefault("ts", time.time())
        data = (json.dumps(pkt) + "\n").encode()
        try:
            raw = socket.create_connection((ip, int(pport)), timeout=5)
            try:
                if self.tls:
                    raw = self._client_ctx().wrap_socket(raw)
                raw.sendall(data)
            finally:
                try: raw.close()
                except OSError: pass
            return True
        except (OSError, ssl.SSLError, ValueError):
            return False

    def send(self, pkt: dict):
        pkt.setdefault("v", 1)
        pkt.setdefault("id", uuid.uuid4().hex[:8])
        pkt.setdefault("from", self.nick)
        pkt.setdefault("uid", self.uid)
        pkt.setdefault("ts", time.time())
        try:
            self.sock.sendto(json.dumps(pkt).encode()[:1400], (BROADCAST, self.port))
        except OSError:
            pass

    def hello(self):      self.send({"type": "hello", "ip": self.local_ip, "pport": self.pport, "appv": VERSION})
    def heartbeat(self):  self.send({"type": "heartbeat", "ip": self.local_ip, "pport": self.pport, "appv": VERSION})
    def bye(self):        self.send({"type": "bye"})
    def msg(self, text):  self.send({"type": "msg", "text": text})
    def dm(self, to, text): self.send({"type": "dm", "to": to, "text": text})  # legacy broadcast DM
    def typing(self):     self.send({"type": "typing"})

    def rename(self, new_nick):
        old = self.nick
        self.nick = new_nick
        self.send({"type": "rename", "old": old, "new": new_nick,
                   "ip": self.local_ip, "pport": self.pport})

    def listen_loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                pkt = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            if pkt.get("uid") == self.uid:
                continue  # ignore echo of ourselves
            pid = pkt.get("id")
            if pid and pid in self.seen_ids:
                continue
            if pid:
                self.seen_ids.add(pid)
                if len(self.seen_ids) > 5000:
                    self.seen_ids.clear()
            self.inbox.put(pkt)

    def heartbeat_loop(self):
        while self.running:
            time.sleep(HEARTBEAT_EVERY)
            if self.running:
                self.heartbeat()


# --------------------------------------------------------------- state ---

class State:
    def __init__(self, me):
        self.me = me
        self.peers = {}   # nick -> {uid, ip, pport, last}
        self.uid2nick = {}  # uid -> nick (to handle renames)
        self.lines = []   # room: (ts, kind, nick, text)  kind: chat/dm/sys/me
        self.priv = {}    # nick -> [(ts, who, text)]  private 1-on-1 threads
        self.unread = {}  # nick -> count of unseen private msgs
        self.view = "room"  # "room" or a nick for private view
        self.typing = {}  # nick -> last typing ts (room)
        self.priv_typing = {}  # nick -> last private-typing ts
        self.newest = {"v": None, "by": None}  # newest peer version seen (update nudge)
        self.lock = threading.Lock()

    def touch_peer(self, nick, uid, ip, pport=None):
        with self.lock:
            old_nick = self.uid2nick.get(uid)
            if old_nick and old_nick != nick:
                self.peers.pop(old_nick, None)
                if old_nick in self.priv:
                    self.priv[nick] = self.priv.pop(old_nick)
                if old_nick in self.unread:
                    self.unread[nick] = self.unread.pop(old_nick)
                if self.view == old_nick:
                    self.view = nick
            self.uid2nick[uid] = nick
            prev = self.peers.get(nick, {})
            self.peers[nick] = {"uid": uid, "ip": ip or prev.get("ip", "?"),
                                "pport": pport or prev.get("pport"),
                                "last": time.time()}

    def peer(self, nick):
        with self.lock:
            return dict(self.peers.get(nick, {}))

    def remove_peer(self, nick):
        with self.lock:
            self.peers.pop(nick, None)

    def prune(self):
        with self.lock:
            now = time.time()
            dead = [n for n, p in self.peers.items() if now - p["last"] > PEER_TIMEOUT]
            for n in dead:
                del self.peers[n]
            tdead = [n for n, t in self.typing.items() if now - t > TYPING_TIMEOUT]
            for n in tdead:
                del self.typing[n]
            pdead = [n for n, t in self.priv_typing.items() if now - t > TYPING_TIMEOUT]
            for n in pdead:
                del self.priv_typing[n]
            return dead

    def add(self, kind, nick, text):
        with self.lock:
            self.lines.append((time.time(), kind, nick, text))
            if len(self.lines) > 500:
                self.lines = self.lines[-500:]

    def add_priv(self, peer, who, text):
        with self.lock:
            self.priv.setdefault(peer, []).append((time.time(), who, text))
            self.priv[peer] = self.priv[peer][-300:]
            if peer != self.view and who != "you":
                self.unread[peer] = self.unread.get(peer, 0) + 1

    def mark_read(self, peer):
        with self.lock:
            self.unread.pop(peer, None)

    def online(self):
        with self.lock:
            return sorted(self.peers.items())


def user_color(nick):
    return (abs(hash(nick)) % len(COLORS)) + 1


def fmt_time(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def vnewer(a, b) -> bool:
    """True if version string a > b ('1.3.0' > '1.2.0'). Garbage-safe."""
    try:
        pa = tuple(int(x) for x in str(a).strip().split("."))
        pb = tuple(int(x) for x in str(b).strip().split("."))
    except (ValueError, AttributeError):
        return False
    return pa > pb


def fetch_latest_version(timeout=6):
    """Ask GitHub what the newest release is. None when offline/failed — never raises."""
    try:
        import urllib.request
        req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": "whisperlan"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(32).decode().strip().split()[0][:16]
    except Exception:
        return None


def github_update_check(st: "State"):
    """Background, cached (24h), silent when offline. Drops a 🔔 line if you're behind."""
    try:
        cache_p = os.path.expanduser("~/.whisper-update")
        now = time.time()
        latest = None
        try:
            with open(cache_p) as f:
                saved_at, saved_v = f.read().strip().split()
                if now - float(saved_at) < 86400:
                    latest = saved_v
        except (OSError, ValueError):
            pass
        if latest is None:
            latest = fetch_latest_version()
            if latest:
                try:
                    with open(cache_p, "w") as f:
                        f.write(f"{now} {latest}")
                except OSError:
                    pass
        if latest and vnewer(latest, VERSION):
            st.add("sys", "", f"── 🔔 whisperlan v{latest} is out (you're on v{VERSION}) ──")
            st.add("sys", "", f"── update: curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash ──")
    except Exception:
        pass


# ----------------------------------------------------------------- ui ---

HELP = """commands:
  /private @name / /p @name   open encrypted private chat 🔒
  /room                       back to the LAN room
  /dm @name <msg>             one private msg (no view switch)
  /name <name>                change your name (/nick works too)
  /me <does...>               action, e.g. /me laughs
  /shrug /flip /unflip /party fun
  /online                     who's here
  /clear                      wipe current view
  /panic                      NUKE all local history (asks first) 💥
  /help                       this help
  /quit                       slip away
in private: just type — it goes 🔒 direct (TLS) to one person, nobody else gets it."""

def pump_packets(net: Net, st: State, bell: list):
    """Drain inbox queues -> state. Returns True if anything changed."""
    changed = False
    # --- private direct (TCP, TLS) first ---
    while True:
        try:
            p = net.priv_inbox.get_nowait()
        except queue.Empty:
            break
        changed = True
        t = p.get("type")
        nick = p.get("from", "?")
        txt = str(p.get("text", ""))[:1000]
        if t == "priv":
            st.add_priv(nick, nick, txt)
            st.add("sys", "", f"── 🔒 private from {nick} (/p @{nick} to reply) ──")
            bell.append(nick)
        elif t == "priv-typing":
            with st.lock:
                st.priv_typing[nick] = time.time()
    while True:
        try:
            p = net.inbox.get_nowait()
        except queue.Empty:
            return changed
        changed = True
        t = p.get("type")
        nick = p.get("from", "?")
        uid = p.get("uid", "?")
        ip = p.get("ip", "?")
        pport = p.get("pport")
        txt = str(p.get("text", ""))[:1000]

        if t in ("hello", "heartbeat"):
            known = nick in dict(st.online())
            st.touch_peer(nick, uid, ip, pport)
            pv = str(p.get("appv", "") or "")[:16]
            if pv and vnewer(pv, VERSION):
                with st.lock:
                    cur = st.newest.get("v")
                    if cur is None or vnewer(pv, cur):
                        st.newest = {"v": pv, "by": nick}
                        fresh = True
                    else:
                        fresh = False
                if fresh:
                    st.add("sys", "", f"── 🔔 {nick} runs whisperlan v{pv} (you: v{VERSION}) — update when you can ──")
            if t == "hello" and not known:
                lock = "🔒" if pport else ""
                st.add("sys", "", f"── {nick} joined from {ip} {lock} ──")
        elif t == "bye":
            st.remove_peer(nick)
            st.add("sys", "", f"── {nick} slipped away ──")
        elif t == "rename":
            old, new = p.get("old", "?"), p.get("new", "?")
            old_info = st.peer(old)  # keep private-contact info across renames
            st.remove_peer(old)
            st.touch_peer(new, uid, ip or old_info.get("ip"),
                          pport or old_info.get("pport"))
            st.add("sys", "", f"── {old} is now {new} ──")
        elif t == "msg":
            st.touch_peer(nick, uid, ip, pport)
            st.add("chat", nick, txt)
            if f"@{st.me}" in txt:
                bell.append(nick)
        elif t == "dm":
            st.touch_peer(nick, uid, ip, pport)
            if p.get("to") == st.me:
                st.add("dm", nick, txt)
                bell.append(nick)
        elif t == "typing":
            with st.lock:
                st.typing[nick] = time.time()
        elif t == "me":
            st.touch_peer(nick, uid, ip, pport)
            st.add("me", nick, txt)


def send_priv_msg(net: Net, st: State, to: str, text: str) -> str:
    """Try encrypted TCP direct. Fallback to legacy broadcast DM. Returns 'tls'|'broadcast'|'offline'."""
    peer = st.peer(to)
    ip, pport = peer.get("ip"), peer.get("pport")
    if ip and ip != "?" and pport:
        if net.send_private(ip, pport, {"type": "priv", "to": to, "text": text}):
            st.add_priv(to, "you", text)
            return "tls"
    # fallback: legacy broadcast DM (warn: not truly private)
    net.dm(to, text)
    st.add_priv(to, "you", text + "  (sent via broadcast — peer offline or old version ⚠️)")
    with st.lock:
        known = to in st.peers
    return "broadcast" if known else "offline"


def draw(stdscr, st: State, net: Net, input_buf, scroll, logf):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    sidebar_w = min(26, max(18, w // 4)) if w > 80 else 0
    main_w = w - sidebar_w
    top_h, input_h = 2, 3
    msg_h = h - top_h - input_h - 1

    with st.lock:
        view = st.view
        unread = dict(st.unread)
        priv_thread = list(st.priv.get(view, [])) if view != "room" else []
        lines = list(st.lines)
        typing = [n for n, t in st.typing.items() if time.time() - t < TYPING_TIMEOUT]
        priv_typing = [n for n, t in st.priv_typing.items() if time.time() - t < TYPING_TIMEOUT]

    # top bar
    n_online = len(st.online()) + 1
    if view == "room":
        title = f" 🤫 whisper  │  room: lan  │  {n_online} online  │  {st.me}@{socket.gethostname()} "
        hint = " type to broadcast │ /p @name for private 🔒 │ /help "
    else:
        lock = "🔒 TLS" if net.tls else "🔒"
        title = f" 🤫 whisper  │  {lock} private with {view}  │  /room to go back  │  {st.me} "
        hint = f" 🔒 typing goes ONLY to {view} │ /room to exit │ /clear "
    stdscr.attron(curses.color_pair(7) | curses.A_BOLD)
    stdscr.addstr(0, 0, title[:w].ljust(w))
    stdscr.attroff(curses.color_pair(7) | curses.A_BOLD)
    try: stdscr.addstr(1, 0, hint.ljust(w)[:w], curses.A_DIM)
    except curses.error: pass

    # messages for current view
    wrapped = []
    if view == "room":
        for ts, kind, nick, text in lines:
            stamp = fmt_time(ts)
            if kind == "sys":
                wrapped.append(("sys", f" {text}"))
            elif kind == "me":
                for ln in textwrap.wrap(f"* {nick} {text}", width=max(20, main_w - len(stamp) - 4)) or [""]:
                    wrapped.append(("me", f"{stamp} {ln}"))
            elif kind == "dm":
                for ln in textwrap.wrap(text, width=max(20, main_w - len(nick) - len(stamp) - 12)) or [""]:
                    wrapped.append(("dm", f"{stamp} 🕵 {nick} → you: {ln}"))
            else:
                for ln in textwrap.wrap(text, width=max(20, main_w - len(nick) - len(stamp) - 5)) or [""]:
                    wrapped.append(("chat", (stamp, nick, ln)))
    else:
        wrapped.append(("sys", f" ── 🔒 encrypted private with {view} — only you two get these ── "))
        for ts, who, text in priv_thread:
            stamp = fmt_time(ts)
            tag = "you" if who == "you" else view
            for ln in textwrap.wrap(text, width=max(20, main_w - len(tag) - len(stamp) - 6)) or [""]:
                wrapped.append(("priv", (stamp, tag, ln, who == "you")))

    max_scroll = max(0, len(wrapped) - msg_h)
    scroll[0] = max(0, min(scroll[0], max_scroll))
    vis = wrapped[len(wrapped) - msg_h - scroll[0]: len(wrapped) - scroll[0] if scroll[0] else len(wrapped)]

    y = top_h + 1
    for item in vis:
        if y >= top_h + 1 + msg_h: break
        try:
            if item[0] == "sys":
                stdscr.addstr(y, 1, item[1][:main_w - 2], curses.color_pair(8) | curses.A_DIM)
            elif item[0] == "me":
                stdscr.addstr(y, 1, item[1][:main_w - 2], curses.color_pair(3))
            elif item[0] == "dm":
                stdscr.addstr(y, 1, item[1][:main_w - 2], curses.A_BOLD)
            elif item[0] == "priv":
                _, (stamp, tag, ln, mine) = item
                x = 1
                stdscr.addstr(y, x, stamp + " ", curses.A_DIM); x += len(stamp) + 1
                stdscr.addstr(y, x, ("🔒 " + tag)[:14].ljust(15) + " ",
                              curses.color_pair(user_color(tag)) | curses.A_BOLD); x += 16
                # readable + distinct: theirs = bright bold, mine = dim
                stdscr.addstr(y, x, ln[:main_w - x - 1],
                              curses.A_BOLD if not mine else curses.A_DIM)
            else:
                _, (stamp, nick, ln) = item
                x = 1
                stdscr.addstr(y, x, stamp + " ", curses.A_DIM); x += len(stamp) + 1
                stdscr.addstr(y, x, nick[:14].ljust(14) + " ", curses.color_pair(user_color(nick)) | curses.A_BOLD); x += 15
                stdscr.addstr(y, x, ln[:main_w - x - 1])
        except curses.error:
            pass
        y += 1

    # typing indicator (view-aware)
    tshow = priv_typing if view != "room" else typing
    if view != "room":
        tshow = [n for n in tshow if n == view]
    if tshow and y < top_h + 1 + msg_h:
        try: stdscr.addstr(y, 1, f"  ✍ {', '.join(tshow[:3])} typing…", curses.A_DIM)
        except curses.error: pass

    # sidebar with unread badges
    if sidebar_w:
        sx = main_w
        try: stdscr.vline(top_h + 1, sx, curses.ACS_VLINE, msg_h)
        except curses.error: pass
        try: stdscr.addstr(top_h + 1, sx + 2, "● ONLINE", curses.A_BOLD | curses.color_pair(2))
        except curses.error: pass
        sy = top_h + 2
        try:
            marker = "➤" if view == "room" else " "
            stdscr.addstr(sy, sx + 2, f"{marker} #lan (room)"[:sidebar_w - 3],
                          curses.A_BOLD if view == "room" else curses.A_DIM); sy += 1
        except curses.error: pass
        try: stdscr.addstr(sy, sx + 2, f"  {st.me} (you)"[:sidebar_w - 3], curses.color_pair(user_color(st.me)) | curses.A_BOLD); sy += 1
        except curses.error: pass
        for nick, info in st.online():
            if sy >= top_h + 1 + msg_h: break
            badge = f" ({unread[nick]})" if nick in unread else ""
            cur = "➤" if view == nick else " "
            label = f"{cur} 🔒{nick}{badge}"[:sidebar_w - 3]
            attr = curses.color_pair(user_color(nick))
            if view == nick: attr |= curses.A_BOLD | curses.A_REVERSE
            elif nick in unread: attr |= curses.A_BOLD
            try: stdscr.addstr(sy, sx + 2, label, attr)
            except curses.error: pass
            sy += 1

    # input box
    iy = h - input_h
    try:
        stdscr.hline(iy - 1, 0, curses.ACS_HLINE, w)
        prefix = f" 🔒→{view} " if view != "room" else f" {st.me} › "
        stdscr.addstr(iy, 0, f"{prefix}{input_buf[0]}"[:w - 1])
        stdscr.move(iy, min(w - 1, len(prefix) + input_buf[1]))
    except curses.error:
        pass
    stdscr.refresh()


def handle_command(cmd, net, st, input_state, stdscr=None):
    parts = cmd.split()
    verb = parts[0].lower() if parts else ""
    if verb in ("/quit", "/q", "/exit"):
        return "quit"
    if verb in ("/nick", "/name") and len(parts) >= 2:
        new = parts[1][:16].strip().lstrip("@")
        if new:
            net.rename(new)
            st.me = new
            st.add("sys", "", f"── you are now {new} ──")
        return None
    if verb in ("/private", "/priv", "/p") and len(parts) >= 2:
        to = parts[1].lstrip("@")
        if to == st.me:
            st.add("sys", "", "── that's you, bro 😅 ──")
            return None
        with st.lock:
            st.view = to
        st.mark_read(to)
        with st.lock:
            st.priv.setdefault(to, [])
        st.add("sys", "", f"── 🔒 private with {to} — type to whisper, /room to exit ──")
        return "switched"
    if verb in ("/room", "/public", "/lobby", "/back"):
        with st.lock:
            st.view = "room"
        st.add("sys", "", "── back in #lan room ──")
        return "switched"
    if verb == "/dm" and len(parts) >= 3:
        to = parts[1].lstrip("@")
        body = " ".join(parts[2:])[:1000]
        res = send_priv_msg(net, st, to, body)
        if res == "tls":
            st.add("sys", "", f"── 🔒 sent privately to {to} ──")
        elif res == "broadcast":
            st.add("sys", "", f"── ⚠️ {to} on old version/offline — sent via broadcast ──")
        else:
            st.add("sys", "", f"── haven't seen {to} yet. wait for them to join, then retry ──")
        return None
    if verb == "/me":
        net.send({"type": "me", "text": cmd[3:].strip()[:500] or "vibes"})
        st.add("me", st.me, cmd[3:].strip()[:500] or "vibes")
        return None
    if verb in FUN:
        net.msg(FUN[verb])
        st.add("chat", st.me, FUN[verb])
        return None
    if verb == "/clear":
        if st.view == "room":
            with st.lock: st.lines.clear()
        else:
            with st.lock: st.priv[st.view] = []
        return None
    if verb == "/online":
        with st.lock:
            tagged = [f"{n} 🔒" if p.get("pport") else n for n, p in st.peers.items()]
        names = ", ".join(sorted(tagged) + [st.me + " (you)"]) or "just you, echo… echo…"
        st.add("sys", "", f"── online: {names} ──")
        return None
    if verb == "/panic":
        now = time.time()
        confirm = len(parts) >= 2 and parts[1].lower() in ("yes", "confirm", "doit", "nuke")
        with st.lock:
            pending = getattr(st, "panic_armed_at", 0)
            fresh = (now - pending) < 30
        if confirm and fresh:
            with st.lock:
                st.lines.clear()
                st.priv.clear()
                st.unread.clear()
                st.typing.clear()
                st.priv_typing.clear()
                st.view = "room"
                st.panic_armed_at = 0
            try:
                open(LOG_PATH, "w").close()  # nuke the on-disk log too
                disk = "memory + log file wiped"
            except OSError:
                disk = "memory wiped (log file locked — delete ~/.whisper.log by hand)"
            st.add("sys", "", f"── 💥 panic done. {disk}. nobody was notified. breathe. ──")
            return "panicked"
        with st.lock:
            st.panic_armed_at = now
        st.add("sys", "", "── ⚠️ /panic wipes ALL local history: room + every private + ~/.whisper.log ──")
        st.add("sys", "", "── this only wipes YOUR machine. others keep their copies. ──")
        st.add("sys", "", "── sure? type  /panic yes  within 30s ──")
        return None
    if verb == "/help":
        for ln in HELP.splitlines(): st.add("sys", "", ln)
        return None
    st.add("sys", "", f"── unknown cmd {verb}. try /help ──")
    return None


def run_tui(net: Net, st: State):
    bell = []
    scroll = [0]
    input_buf = ["", 0]  # [text, cursor]
    logf = open(LOG_PATH, "a")

    def loop(stdscr):
        curses.curs_set(1)
        curses.start_color(); curses.use_default_colors()
        for i, c in enumerate(COLORS, start=1):
            try: curses.init_pair(i, c, -1)
            except curses.error: pass
        try:
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(8, curses.COLOR_WHITE, -1)
        except curses.error: pass
        stdscr.nodelay(True); stdscr.keypad(True)
        stdscr.timeout(100)
        last_type_sent = 0
        last_prune = time.time()

        st.add("sys", "", "── 🤫 welcome to whisper. softly shouting HELLO to the LAN… ──")
        st.add("sys", "", "── room = everyone. /p @name = encrypted private 🔒 ──")
        for ln in HELP.splitlines(): st.add("sys", "", ln)
        net.hello()
        threading.Thread(target=github_update_check, args=(st,), daemon=True).start()

        while True:
            if pump_packets(net, st, bell):
                scroll_keep = scroll[0] == 0
                if not scroll_keep: pass
            if bell:
                stdscr.addstr(0, 0, ""); curses.beep()
                bell.clear()
            if time.time() - last_prune > 5:
                for d in st.prune():
                    st.add("sys", "", f"── {d} timed out ──")
                    if st.view == d:
                        st.add("sys", "", f"── {d} left… /room to go back ──")
                last_prune = time.time()

            draw(stdscr, st, net, input_buf, scroll, logf)

            try: ch = stdscr.get_wch()
            except curses.error: continue
            if ch is None: continue

            text, cur = input_buf
            if isinstance(ch, str) and ch in ("\n", "\r"):
                line = text.strip()
                input_buf[0], input_buf[1] = "", 0
                scroll[0] = 0
                if not line: continue
                if line.startswith("/"):
                    res = handle_command(line, net, st, input_buf)
                    if res == "quit":
                        break
                    if res in ("switched", "panicked"):
                        scroll[0] = 0
                    continue
                # plain text: private view -> encrypted direct, room -> broadcast
                if st.view != "room":
                    to = st.view
                    logf.write(f"[{fmt_time(time.time())}] {st.me} 🔒→{to}: {line}\n"); logf.flush()
                    res = send_priv_msg(net, st, to, line)
                    if res == "offline":
                        st.add("sys", "", f"── {to} not here yet — they’ll see room msgs, not this ──")
                    elif res == "broadcast":
                        st.add("sys", "", f"── ⚠️ sent via broadcast (peer on old version) ──")
                else:
                    logf.write(f"[{fmt_time(time.time())}] {st.me}: {line}\n"); logf.flush()
                    if line.startswith("@"):
                        # "@bob hello" shortcut = private without switching
                        sp = line.split(None, 1)
                        if len(sp) == 2 and sp[0].lstrip("@"):
                            res = send_priv_msg(net, st, sp[0].lstrip("@"), sp[1])
                            if res == "tls":
                                st.add("sys", "", f"── 🔒 sent privately to {sp[0].lstrip('@')} ──")
                                continue
                            # fall through to broadcast if offline
                    net.msg(line)
                    st.add("chat", st.me, line)
            elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b", "\x08"):
                if cur > 0:
                    input_buf[0] = text[:cur - 1] + text[cur:]
                    input_buf[1] = cur - 1
            elif ch == curses.KEY_LEFT:  input_buf[1] = max(0, cur - 1)
            elif ch == curses.KEY_RIGHT: input_buf[1] = min(len(text), cur + 1)
            elif ch == curses.KEY_UP:    scroll[0] = min(scroll[0] + 1, 500)
            elif ch == curses.KEY_DOWN:  scroll[0] = max(scroll[0] - 1, 0)
            elif ch == "\x15":  # ctrl-U clear line
                input_buf[0], input_buf[1] = "", 0
            elif ch == "\x03":  # ctrl-C
                break
            elif isinstance(ch, str) and len(ch) == 1 and ch.isprintable():
                input_buf[0] = text[:cur] + ch + text[cur:]
                input_buf[1] = cur + 1
                if time.time() - last_type_sent > 2:
                    if st.view != "room":
                        peer = st.peer(st.view)
                        if peer.get("ip") and peer.get("pport"):
                            net.send_private(peer["ip"], peer["pport"], {"type": "priv-typing"})
                    else:
                        net.typing()
                    last_type_sent = time.time()

    try:
        curses.wrapper(loop)
    finally:
        logf.close()


def main():
    ap = argparse.ArgumentParser(
        prog="whisper",
        description="🤫 whisperlan — terminal whispers for your LAN. Same WiFi, no server, just type.")
    ap.add_argument("--name", "-n", default=None, help="your display name (e.g. --name ali)")
    ap.add_argument("--nick", default=None, help=argparse.SUPPRESS)  # old alias, hidden
    ap.add_argument("--port", "-p", type=int, default=PORT_DEFAULT, help="LAN room port (default 54545, must match friends)")
    ap.add_argument("--pport", type=int, default=None, help="private chat port (default: --port+1)")
    ap.add_argument("--version", "-v", action="store_true", help="print version and exit")
    args = ap.parse_args()

    if args.version:
        print(f"whisperlan {VERSION}")
        return

    nick = (args.name or args.nick
            or os.environ.get("WHISPER_NAME") or os.environ.get("WHISPER_NICK")
            or os.environ.get("GHOSTLINE_NICK")
            or getpass.getuser()).strip().replace(" ", "_")[:16] or f"anon-{random.randint(100,999)}"

    print("\n".join(BANNER))
    print(f"\n  🤫 whisperlan {VERSION}  ·  you are: {nick}  ·  room port: {args.port}")
    print("  room = broadcast to LAN · private = 🔒 TLS direct (only you two get it)")
    print("  softly broadcasting HELLO to your LAN… (ctrl-C to slip away)\n")

    net = Net(nick, args.port, args.pport)
    print(f"  🔒 private inbox listening on {net.local_ip}:{net.pport} "
          f"({'TLS on' if net.tls else 'TLS off (no openssl) — still unicast, not broadcast'})\n")
    st = State(nick)
    threading.Thread(target=net.listen_loop, daemon=True).start()
    threading.Thread(target=net.heartbeat_loop, daemon=True).start()
    try:
        run_tui(net, st)
    except KeyboardInterrupt:
        pass
    finally:
        net.running = False
        try: net.bye()
        except OSError: pass
        try: net._priv_srv.close()
        except (OSError, AttributeError): pass
        try: net.sock.close()
        except (OSError, AttributeError): pass
        time.sleep(0.2)
        print("\n  🤫 you slipped away quietly. bye.\n")


if __name__ == "__main__":
    main()
