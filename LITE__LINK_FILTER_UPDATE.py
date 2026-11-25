# -*- coding: utf-8 -*-
"""
============================================================
  FRESH FILTER BOT  (READY TO RUN - FAST, Max Threads, deep check OFF)
  Advanced duplicate link detector + Regex Filters + Auto Merge (merge_links.db)
============================================================
PythonAnywhere par chalane ke liye:
  1. Bash console: pip install --user pyTelegramBotAPI requests
  2. Is file ke saath ek ".env" file banao (neeche example):
         BOT_TOKEN=123456:ABC...
         OWNER_ID=5214795662
         DEEP_CHECK=1        (free account par 0 rakho, neeche note dekho)
  3. Tasks tab -> "Always-on tasks" (paid account) me command do:
         python3 /home/YOURUSERNAME/bot/advance_lite_link_filter_pa.py
     Free account me always-on task nahi milta, bot 24x7 nahi chalega.
  Note: Free account par sirf whitelist wali sites khulti hain, isliye
        file-naam wala deep link check kaam nahi karega (DEEP_CHECK=0).
        Link-key wala duplicate check phir bhi poora chalega.
"""
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telebot.apihelper import ApiTelegramException
import json
import os
import re
import time
import random
import string
import threading
import queue
import uuid
import traceback
import sys
import signal
import atexit
import sqlite3
import html as htmlmod
try:
    import fcntl            # Linux (PythonAnywhere) par single-instance lock ke liye
except ImportError:
    fcntl = None
from functools import partial, wraps
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from http.cookiejar import DefaultCookiePolicy
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs, urlencode

try:
    import requests
except ImportError:          # pip install requests
    requests = None

BOT_NAME = "fresh filter bot"
OFFICIAL_CHANNEL = "https://t.me/+bBm7s1JtbcM0NmJl"

# ============================================================
# PYTHONANYWHERE SETUP: folder, .env, line-buffered logs
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # always-on task ka cwd alag ho sakta hai


def _load_env_file():
    """BASE_DIR/.env se KEY=VALUE padho (extra package ki zaroorat nahi)."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as e:
        print(f".env padhne me error: {e}")


_load_env_file()
try:
    sys.stdout.reconfigure(line_buffering=True)            # PythonAnywhere logs me turant dikhe
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================
TOKEN = os.environ.get("BOT_TOKEN") or "7310687696:AAGUnJYQHFb6DR37orvp8SG7iXYospWoVNw"
try:
    OWNER_ID = int(os.environ.get("OWNER_ID") or 5214795662)      # apna Telegram user ID
except ValueError:
    OWNER_ID = 5214795662

DB_FILE = os.path.join(BASE_DIR, "merge_links.db")   # FINAL database: baaki sab .json/.db files isme merge hoti hain
MERGE_DIRS = [BASE_DIR, os.path.join(BASE_DIR, "merge_in")]   # in folders ki .json/.db files merge hongi
MERGE_EXT = (".json", ".json.migrated", ".db", ".sqlite")
DB_EXISTED = os.path.exists(DB_FILE)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
JOURNAL_FILE = os.path.join(BASE_DIR, "pending.json")
LOCK_FILE = os.path.join(BASE_DIR, "bot.lock")

LEGACY_URL_RE = re.compile(r'(https?://[^\s]+)')       # sirf purani DB import ke liye
_ZW = "\u200b\u200c\u200d\u2060\ufeff"                   # zero-width chars (copy-paste me chhupe rehte hain)
_TB = r"(?:terabox|1024tera|teraboxapp|4funbox|mirrobox|nephobox|momerybox|tibibox|terafileshare)"
# http(s):// (kisi bhi case me) | www. | bina https wale terabox-family links; < > aur zero-width URL me nahi aate
URL_RE = re.compile(
    r"((?:https?://|(?<![\w@/.-])(?:www\.|[a-z0-9.-]*" + _TB + r"[a-z0-9-]*\.[a-z]{2,}/))"
    r"[^\s<>" + _ZW + r"]+)", re.I)
TRAIL = '.,;:!?)]}"\''
TERABOX_WORDS = ("terabox", "1024tera", "teraboxapp", "4funbox", "mirrobox",
                 "nephobox", "momerybox", "tibibox", "terafileshare")
ID_LINK_SITES = ("filesplay", "filenext")     # inka title masked hota hai, isliye title-check nahi lagta
GENERIC_TITLES = {"terabox", "diskwala", "login", "justamoment", "accessdenied", "error",
                  "403forbidden", "404notfound", "attentionrequired", "redirecting",
                  "sharefile", "shareddocument", "download", "home"}
FILE_EXT = r"\.(mp4|mkv|avi|mov|webm|zip|rar|7z|pdf|mp3|apk|jpg|png|epub|txt)$"

DEEP_CHECK = False           # FAST MODE: deep check hamesha OFF (sirf link-key se duplicate check)
DEEP_TIMEOUT = 6             # ek request ka max time (sec)
DEEP_TOTAL_TIMEOUT = 12      # ek post ke saare links ka max time (sec)
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}

ALERT_TTL = 10               # alert message itne sec baad auto-delete
CHANNEL_GAP = 3.0            # channel me bhejne ke beech ka gap (Telegram limit 20 msg/min)
CHANNEL_GAP_MIN = 2.0
CHANNEL_GAP_MAX = 5.0
PRIVATE_GAP = 0.6
MAX_POST_ATTEMPTS = 30
NET_RETRY_MAX = 20

HTTP = None
if requests:
    HTTP = requests.Session()
    HTTP.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    _ad = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=120)
    HTTP.mount("https://", _ad)
    HTTP.mount("http://", _ad)

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=12)

DB_LOCK = threading.RLock()
ALERT_LOCK = threading.Lock()
SETTINGS_LOCK = threading.RLock()
ALERT_STORE = {}
POST_POOL = ThreadPoolExecutor(max_workers=16)
FETCH_POOL = ThreadPoolExecutor(max_workers=24)
START_TIME = time.time()

NET_ERRORS = (requests.exceptions.RequestException,) if requests else (OSError,)

STATS = {"received": 0, "replied": 0, "channel_sent": 0, "dup_blocked": 0,
         "links_removed": 0, "non_text_deleted": 0, "dropped": 0, "failed": 0, "restored": 0}
TIMING = {"proc": [0.0, 0], "fetch": [0.0, 0]}
STATS_LOCK = threading.Lock()
GAP_NOW = {}


def _stat(key, n=1):
    with STATS_LOCK:
        STATS[key] += n


def _timing(key, dt):
    with STATS_LOCK:
        TIMING[key][0] += dt
        TIMING[key][1] += 1


def _avg(key):
    with STATS_LOCK:
        total, n = TIMING[key]
    return f"{total / n:.1f} sec ({n} baar)" if n else "-"


# ============================================================
# SETTINGS
# ============================================================
def load_settings():
    d = {
        "header": "",
        "footer": "",
        "button_text": "Join Channel",
        "button_url": "",
        "channels": [],
        "my_channel_link": "",
        "unique_code_status": True,
        "link_replace_status": True,
        "alert_replace": True,       # ♻️ replace link alert
        "alert_duplicate": True,     # 🚫 duplicate links alert
        "alert_clear": True,         # 🧹 clear of links alert
        "format_status": True,
        "disabled_formats": [],     # /remove_format se band kiye gaye format numbers
        "admins": [],
        "regex_filters": [],         # [{"t": "re"|"wild", "p": pattern}]
        "non_text_list": [],
        "surrounding_text_status": True,   # /enable_text (ON, default) | /disable_text (OFF: sirf links jayenge)
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                d.update(saved)
        except Exception as e:
            print(f"Settings load error: {e}")
    return d


def save_settings():
    with SETTINGS_LOCK:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bot_data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)


bot_data = load_settings()


# ============================================================
# SQLITE LINK DATABASE (lite)
# ============================================================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS owners(key TEXT PRIMARY KEY, owner TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_owners_owner ON owners(owner);
CREATE TABLE IF NOT EXISTS titles(tkey TEXT PRIMARY KEY, owner TEXT NOT NULL, key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_titles_key ON titles(key);
CREATE TABLE IF NOT EXISTS codes(code TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS merged_files(sig TEXT PRIMARY KEY, name TEXT, ts INTEGER);
"""


class LinkDB:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=DELETE")      # PythonAnywhere ke file system par WAL se bachne ke liye
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(_SCHEMA)

    def _q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _tx(self, fn):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                fn(self.conn)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def owner_of(self, key):
        r = self._q("SELECT owner FROM owners WHERE key=?", (key,))
        return r[0][0] if r else None

    def title_info(self, tkey):
        r = self._q("SELECT owner, key FROM titles WHERE tkey=?", (tkey,))
        return (r[0][0], r[0][1]) if r else None

    def owned_keys(self, owner):
        return [r[0] for r in self._q("SELECT key FROM owners WHERE owner=?", (owner,))]

    def forget_keys(self, keys):
        def run(c):
            for k in keys:
                c.execute("DELETE FROM owners WHERE key=?", (k,))
                c.execute("DELETE FROM titles WHERE key=?", (k,))
        self._tx(run)

    def save_links(self, items, titles):
        def run(c):
            c.executemany("INSERT OR REPLACE INTO owners(key, owner) VALUES(?, ?)", items)
            c.executemany("INSERT OR REPLACE INTO titles(tkey, owner, key) VALUES(?, ?, ?)", titles)
        self._tx(run)

    def new_code(self):
        with self.lock:
            while True:
                code = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
                cur = self.conn.execute("INSERT OR IGNORE INTO codes(code) VALUES(?)", (code,))
                if cur.rowcount == 1:
                    return code

    def count_links(self):
        return self._q("SELECT COUNT(*) FROM owners")[0][0]

    def clear_all(self):
        def run(c):
            c.execute("DELETE FROM owners")
            c.execute("DELETE FROM titles")
        self._tx(run)

    def count_codes(self):
        return self._q("SELECT COUNT(*) FROM codes")[0][0]

    def import_chunk(self, owners, titles, codes):
        """Merge: jo pehle se hai use chhedta nahi (INSERT OR IGNORE)."""
        def run(c):
            if owners:
                c.executemany("INSERT OR IGNORE INTO owners(key, owner) VALUES(?, ?)", owners)
            if titles:
                c.executemany("INSERT OR IGNORE INTO titles(tkey, owner, key) VALUES(?, ?, ?)", titles)
            if codes:
                c.executemany("INSERT OR IGNORE INTO codes(code) VALUES(?)", [(x,) for x in codes])
        self._tx(run)

    def is_merged(self, sig):
        return bool(self._q("SELECT 1 FROM merged_files WHERE sig=?", (sig,)))

    def mark_merged(self, sig, name):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO merged_files(sig, name, ts) VALUES(?, ?, ?)",
                              (sig, name, int(time.time())))

    def close(self):
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.close()
            except Exception:
                pass


LINKDB = LinkDB(DB_FILE)
atexit.register(LINKDB.close)
try:
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))      # stop/kill par bhi atexit chale
except Exception:
    pass


# ============================================================
# PENDING JOURNAL  (crash/restart par bhi post nahi khoti)
# ============================================================
JOURNAL_LOCK = threading.Lock()
JOURNAL = {"posts": {}, "jobs": {}}
INFLIGHT = set()


def _journal_load():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            JOURNAL["posts"] = dict(data.get("posts", {}))
            JOURNAL["jobs"] = dict(data.get("jobs", {}))
        except Exception as e:
            print(f"Journal load error: {e}")
    for k in JOURNAL["posts"]:
        INFLIGHT.add(k)
    for j in JOURNAL["jobs"].values():
        INFLIGHT.add(j.get("pk"))


def _journal_save():          # hamesha JOURNAL_LOCK ke andar
    tmp = JOURNAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(JOURNAL, f)
    os.replace(tmp, JOURNAL_FILE)


def _msg_dict(message):
    d = getattr(message, "json", None)
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = None
    return d if isinstance(d, dict) else None


def journal_add_post(key, message):
    d = _msg_dict(message)
    if d is None:
        return
    try:
        with JOURNAL_LOCK:
            JOURNAL["posts"][key] = d
            _journal_save()
    except Exception as e:
        print(f"Journal add error: {e}")


def journal_remove_post(key):
    try:
        with JOURNAL_LOCK:
            if JOURNAL["posts"].pop(key, None) is not None:
                _journal_save()
    except Exception as e:
        print(f"Journal remove error: {e}")


def journal_remove_job(jid):
    try:
        with JOURNAL_LOCK:
            if JOURNAL["jobs"].pop(jid, None) is not None:
                _journal_save()
    except Exception as e:
        print(f"Journal job remove error: {e}")


def journal_commit(key, jobs):
    """Post ko 'process baaki' se hata kar uske send-jobs ek saath save karo."""
    with JOURNAL_LOCK:
        old = JOURNAL["posts"].pop(key, None)
        for j in jobs:
            JOURNAL["jobs"][j["id"]] = j
        try:
            _journal_save()
        except Exception:
            for j in jobs:
                JOURNAL["jobs"].pop(j["id"], None)
            if old is not None:
                JOURNAL["posts"][key] = old
            raise


_journal_load()


# ============================================================
# SEND QUEUE + FLOOD CONTROL
# ============================================================
SEND_QUEUES = {}
SEND_LOCK = threading.Lock()


def _gap_for(target):
    return PRIVATE_GAP if (isinstance(target, int) and target > 0) else CHANNEL_GAP


def _retry_after(e):
    try:
        return int(e.result_json.get("parameters", {}).get("retry_after", 5))
    except Exception:
        return 5


def _giveup(fn):
    cb = getattr(fn, "on_giveup", None)
    if cb:
        try:
            cb()
        except Exception as e:
            print(f"Giveup callback error: {e}")


def _send_worker(target, q):
    gap = _gap_for(target)
    adaptive = not (isinstance(target, int) and target > 0)
    ok_streak = 0
    while True:
        fn = q.get()
        t_job = time.time()
        hit_limit = False
        try:
            done, permanent, net_tries = False, False, 0
            while True:
                try:
                    fn()
                    done = True
                    break
                except ApiTelegramException as e:
                    if e.error_code == 429:
                        hit_limit = True
                        if adaptive:
                            gap = min(CHANNEL_GAP_MAX, gap + 0.5)
                            ok_streak = 0
                        wait = _retry_after(e) + 1
                        print(f"Rate limit ({target}): {wait} sec ruk raha hoon...")
                        time.sleep(wait)
                        continue
                    if e.error_code and e.error_code >= 500:
                        net_tries += 1
                        if net_tries >= NET_RETRY_MAX:
                            break
                        time.sleep(min(30, 2 * net_tries))
                        continue
                    print(f"Send error to {target}: {e}")
                    permanent = True
                    break
                except NET_ERRORS as e:
                    net_tries += 1
                    print(f"Network error ({target}) try {net_tries}/{NET_RETRY_MAX}: {e}")
                    if net_tries >= NET_RETRY_MAX:
                        break
                    time.sleep(min(30, 2 * net_tries))
                    continue
                except Exception as e:
                    print(f"Send error to {target}: {e}")
                    permanent = True
                    break
            if not done:
                _stat("dropped")
                print(f"DROPPED: {target} ko ek message nahi ja saka")
                if permanent:
                    _giveup(fn)
        finally:
            q.task_done()
        if adaptive and not hit_limit:
            ok_streak += 1
            if ok_streak >= 30:
                gap = max(CHANNEL_GAP_MIN, gap - 0.2)
                ok_streak = 0
        GAP_NOW[str(target)] = gap
        rest = gap - (time.time() - t_job)
        if rest > 0:
            time.sleep(rest)


def enqueue_send(target, fn):
    key = str(target)
    with SEND_LOCK:
        q = SEND_QUEUES.get(key)
        if q is None:
            q = queue.Queue()
            SEND_QUEUES[key] = q
            threading.Thread(target=_send_worker, args=(target, q), daemon=True).start()
    q.put(fn)


def send_now(target, fn):
    """Turant bhejo. 429/network/server error par queue me jaakar retry hota hai."""
    try:
        fn()
    except ApiTelegramException as e:
        if e.error_code == 429 or (e.error_code and e.error_code >= 500):
            enqueue_send(target, fn)
        else:
            print(f"Send error to {target}: {e}")
            _stat("dropped")
            _giveup(fn)
    except NET_ERRORS as e:
        print(f"Network error to {target}: {e} -> queue me retry")
        enqueue_send(target, fn)
    except Exception as e:
        print(f"Send error to {target}: {e}")
        _stat("dropped")
        _giveup(fn)


def _send_post_inner(target, message, text, markup, reply, fmt):
    ct = message.content_type
    done = False
    if ct == 'text':
        if text:
            if reply:
                try:
                    bot.reply_to(message, text, reply_markup=markup, parse_mode=fmt,
                                 disable_web_page_preview=True)
                except ApiTelegramException as e:
                    if e.error_code == 400 and "repl" in str(e).lower():
                        bot.send_message(target, text, reply_markup=markup, parse_mode=fmt,
                                         disable_web_page_preview=True)
                    else:
                        raise
            else:
                bot.send_message(target, text, reply_markup=markup, parse_mode=fmt,
                                 disable_web_page_preview=True)
            done = True
    elif ct == 'photo':
        bot.send_photo(target, message.photo[-1].file_id, caption=text[:1024], reply_markup=markup, parse_mode=fmt)
        done = True
    elif ct == 'video':
        bot.send_video(target, message.video.file_id, caption=text[:1024], reply_markup=markup, parse_mode=fmt)
        done = True
    elif ct == 'document':
        bot.send_document(target, message.document.file_id, caption=text[:1024], reply_markup=markup, parse_mode=fmt)
        done = True
    elif ct == 'animation':
        bot.send_animation(target, message.animation.file_id, caption=text[:1024], reply_markup=markup, parse_mode=fmt)
        done = True
    elif ct == 'audio':
        bot.send_audio(target, message.audio.file_id, caption=text[:1024], reply_markup=markup, parse_mode=fmt)
        done = True
    if done:
        _stat("replied" if reply else "channel_sent")


def _send_post(target, message, text, markup, reply=False, fmt=None):
    try:
        _send_post_inner(target, message, text, markup, reply, fmt)
    except ApiTelegramException as e:
        # Formatting galat ho to post rukti nahi, plain text me chali jaati hai
        if fmt and e.error_code == 400 and "parse" in str(e).lower():
            print(f"Formatting error, plain text me bhej raha hoon: {e}")
            _send_post_inner(target, message, html_to_plain(text), markup, reply, None)
        else:
            raise


def make_job(pk, message, target, text, markup_spec, reply, fmt=None):
    return {"id": uuid.uuid4().hex[:12], "pk": pk, "target": target, "reply": reply,
            "text": text, "markup": markup_spec, "msg": _msg_dict(message), "fmt": fmt}


def _job_fn(job, message):
    def fn():
        spec = job.get("markup")
        markup = InlineKeyboardMarkup()
        if spec:
            markup.add(InlineKeyboardButton(spec[0], url=spec[1]))
        _send_post(job["target"], message, job["text"], markup, job["reply"], job.get("fmt"))
        journal_remove_job(job["id"])          # bhej diya -> pending se hatao
    fn.on_giveup = lambda: journal_remove_job(job["id"])
    return fn


def dispatch_job(job, message, fast=False):
    try:
        fn = _job_fn(job, message)
        if fast:
            send_now(job["target"], fn)
        else:
            enqueue_send(job["target"], fn)
    except Exception as e:
        print(f"Dispatch error: {e}")


# ============================================================
# HELPERS + 3 MAIN ALERT SYSTEM
# ============================================================
def _run_safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        print(f"Handler error: {e}")
        traceback.print_exc()


def _later(delay, fn):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()


def alert_on(key):
    return bot_data.get(key, True)


def alert_button(message, title, detail, ttl=ALERT_TTL):
    """Chhota inline-button alert: button dabane par popup me poori jankari, ttl sec baad auto-delete."""
    aid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    with ALERT_LOCK:
        ALERT_STORE[aid] = detail[:200]
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(title[:60], callback_data=f"al:{aid}"))

    def _do():
        try:
            sent = bot.reply_to(message, "⚠️", reply_markup=kb)
        except ApiTelegramException as e:
            if e.error_code != 429:
                with ALERT_LOCK:
                    ALERT_STORE.pop(aid, None)
            raise
        if ttl > 0:
            def _auto_delete():
                try:
                    bot.delete_message(sent.chat.id, sent.message_id)
                except Exception:
                    pass
            _later(ttl, _auto_delete)

            def _forget():
                with ALERT_LOCK:
                    ALERT_STORE.pop(aid, None)
            _later(ttl + 5, _forget)

    send_now(message.chat.id, _do)


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith("al:"))
def show_alert(call):
    with ALERT_LOCK:
        text = ALERT_STORE.get(call.data[3:])
    try:
        bot.answer_callback_query(call.id, text or "Ye alert expire ho gaya.", show_alert=True)
    except Exception as e:
        print(f"Callback answer error: {e}")


def notify_owner(text):
    if not OWNER_ID:
        return
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        enqueue_send(OWNER_ID, partial(bot.send_message, OWNER_ID, c, disable_web_page_preview=True))


# ============================================================
# ADVANCED DUPLICATE LINK DETECTOR
# ============================================================
_OWN_CACHE = (None, frozenset())


def own_link_keys():
    """Bot ke apne links (header/footer/button/my link) kabhi duplicate nahi maane jaate."""
    global _OWN_CACHE
    sig = (bot_data.get("header"), bot_data.get("footer"),
           bot_data.get("button_url"), bot_data.get("my_channel_link"))
    if _OWN_CACHE[0] != sig:
        keys = set()
        for t in sig:
            for u in URL_RE.findall(t or ""):
                keys.add(link_key(u.rstrip(TRAIL)))
        _OWN_CACHE = (sig, frozenset(keys))
    return _OWN_CACHE[1]


def is_content_link(url):
    low = url.lower()
    if "t.me" in low or "telegram.me" in low:
        return False
    return link_key(url) not in own_link_keys()


def link_key(url):
    """Link ki pehchaan: http/https, www, trailing slash, utm/fbclid, query order sab ignore."""
    for ch in _ZW:
        url = url.replace(ch, "")
    url = url.strip().strip("<>").rstrip(TRAIL)
    if "://" not in url:                                  # bina https wale link (www.x.com/..., terabox.com/s/..)
        url = "http://" + url
    p = urlparse(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    q = parse_qs(p.query, keep_blank_values=True)

    if any(w in host for w in TERABOX_WORDS):            # terabox ke saare mirror domains = same file
        m = re.search(r"/s/([A-Za-z0-9_-]+)", path)
        if m:
            sid = m.group(1)
            return "terabox:" + (sid[1:] if sid.startswith("1") else sid)
        surl = q.get("surl", [""])[0]
        if surl:
            return "terabox:" + surl

    for fam in ID_LINK_SITES:
        if fam in host:
            m = re.search(r"/s/(?:view/)?([A-Za-z0-9_-]+)", p.path)
            if m and m.group(1) != "view":
                return f"{fam}:{m.group(1)}"

    skip = ("fbclid", "gclid", "ref", "ref_src", "igshid", "si", "mc_cid", "mc_eid")
    pairs = sorted((k, v[0]) for k, v in q.items()
                   if not k.lower().startswith("utm_") and k.lower() not in skip)
    query = urlencode(pairs)
    return host + path + (f"?{query}" if query else "")


def normalize_title(title):
    title = title.strip().lower()
    title = re.sub(FILE_EXT, "", title)
    return re.sub(r"[\W_]+", "", title)


def terabox_title(url):
    p = urlparse(url)
    m = re.search(r"/s/([A-Za-z0-9_-]+)", p.path)
    sid = m.group(1) if m else ("1" + parse_qs(p.query).get("surl", [""])[0])
    if sid in ("", "1"):
        return None
    for host in ("https://www.terabox.com", "https://www.1024terabox.com"):
        try:
            r = HTTP.get(f"{host}/api/shorturlinfo", timeout=DEEP_TIMEOUT, headers=HTTP_HEADERS,
                         params={"app_id": "250528", "shorturl": sid, "root": "1"})
            for it in (r.json().get("list") or []):
                name = it.get("server_filename") or it.get("name")
                if name:
                    return name
        except Exception:
            continue
    return None


_PROXY_FAILS = 0


def _deep_ok():
    global _PROXY_FAILS
    _PROXY_FAILS = 0


def _deep_proxy_fail():
    """Free PythonAnywhere par site block ho to baar-baar try na kare: 8 fail ke baad deep check band."""
    global _PROXY_FAILS, DEEP_CHECK
    _PROXY_FAILS += 1
    if _PROXY_FAILS >= 8 and DEEP_CHECK:
        DEEP_CHECK = False
        print("Deep check band: sites proxy se block ho rahi hain (free PythonAnywhere).")
        notify_owner("⚠️ Deep link check (file-naam) auto-OFF ho gaya kyunki PythonAnywhere ne sites block ki.\n"
                     "Link-key wala duplicate check chalta rahega. .env me DEEP_CHECK=0 likh do.")


def fetch_title_key(url):
    if requests is None:
        return None
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        if any(w in host for w in ID_LINK_SITES):
            return None
        title = terabox_title(url) if any(w in host for w in TERABOX_WORDS) else None
        if not title:
            r = HTTP.get(url.rstrip(TRAIL), timeout=DEEP_TIMEOUT, allow_redirects=True, headers=HTTP_HEADERS)
            if r.status_code != 200:
                return None
            page = r.text[:200000]
            m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I)
                 or re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S))
            if not m:
                return None
            sld = host.split(".")[0]
            title = " ".join(w for w in unescape(m.group(1)).split() if sld not in w.lower())
        _deep_ok()
        norm = normalize_title(title)
        if len(norm) < 8 or norm in GENERIC_TITLES:
            return None
        return norm
    except Exception as e:
        if requests is not None and isinstance(e, requests.exceptions.ProxyError):
            _deep_proxy_fail()
        else:
            print(f"Title fetch error: {e}")
        return None


# Title cache: safal result 1 din, fail 2 minute
TITLE_CACHE = {}
TITLE_CACHE_LOCK = threading.Lock()
TITLE_OK_TTL, TITLE_FAIL_TTL, TITLE_CACHE_MAX = 86400, 120, 5000


def _cache_get(k):
    with TITLE_CACHE_LOCK:
        item = TITLE_CACHE.get(k)
    if not item:
        return False, None
    val, ts = item
    if time.time() - ts > (TITLE_OK_TTL if val else TITLE_FAIL_TTL):
        return False, None
    return True, val


def _cache_put(k, val):
    with TITLE_CACHE_LOCK:
        TITLE_CACHE[k] = (val, time.time())
        if len(TITLE_CACHE) > TITLE_CACHE_MAX:
            for old in list(TITLE_CACHE)[:TITLE_CACHE_MAX // 5]:
                TITLE_CACHE.pop(old, None)


def _fetch_and_cache(k, url):
    val = fetch_title_key(url)
    _cache_put(k, val)
    return val


def fetch_titles(urls):
    out = {}
    if not (DEEP_CHECK and requests):
        return out
    uniq = {}
    for u in urls:
        uniq.setdefault(link_key(u), u)
    todo = {}
    for k, u in uniq.items():
        found, val = _cache_get(k)
        if found:
            if val:
                out[k] = val
        else:
            todo[k] = u
    if not todo:
        return out
    t0 = time.time()
    futs = {k: FETCH_POOL.submit(_fetch_and_cache, k, u) for k, u in todo.items()}
    deadline = t0 + DEEP_TOTAL_TIMEOUT
    for k, f in futs.items():
        try:
            t = f.result(timeout=max(0.1, deadline - time.time()))
        except Exception:
            t = None
        if t:
            out[k] = t
    _timing("fetch", time.time() - t0)
    return out


def strip_duplicate_links(text, check):
    """Sirf duplicate link/line hatao, baaki post waisi hi rahe."""
    seen_keys, removed, new = set(), [], []

    def repl(m):
        raw = m.group(1)
        url = raw.rstrip(TRAIL)
        if not url or not is_content_link(url):
            return raw
        k = link_key(url)
        reason = "Isi post me link dobara aaya" if k in seen_keys else check(k)
        if reason:
            removed.append((url, reason))
            return "\x00"
        seen_keys.add(k)
        new.append((k, url))
        return raw

    new_text = URL_RE.sub(repl, text)
    if not removed:
        return text, removed, new
    lines = []
    for line in new_text.split("\n"):
        if "\x00" in line:
            line = line.replace("\x00", "")
            if line.strip() == "":
                continue
            line = line.rstrip()
        lines.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return cleaned, removed, new


def extract_extra_links(message):
    """Hyperlink (text_link) aur inline-button ke andar chhupe links."""
    urls = []
    for ents in (message.entities, message.caption_entities):
        for e in ents or []:
            if e.type == "text_link" and e.url:
                urls.append(e.url)
    rm = message.reply_markup
    rows = (getattr(rm, "keyboard", None) or getattr(rm, "inline_keyboard", None)) if rm is not None else None
    if rows:
        for row in rows:
            for b in row:
                if getattr(b, "url", None):
                    urls.append(b.url)
    return [u for u in urls if is_content_link(u)]


def is_saved(key, owner):
    o = LINKDB.owner_of(key)
    return o is not None and o != owner


def analyze(message, owner, edited=False):
    text = message.text or message.caption or ""
    extra = extract_extra_links(message)
    plain_urls = [u.rstrip(TRAIL) for u in URL_RE.findall(text)]
    plain_urls = [u for u in plain_urls if is_content_link(u)]
    all_urls = plain_urls + extra

    unknown = [u for u in all_urls if LINKDB.owner_of(link_key(u)) is None]
    titles = fetch_titles(unknown)

    with DB_LOCK:
        def check(k):
            if is_saved(k, owner):
                return "Same link pehle aa chuka hai"
            t = titles.get(k)
            info = LINKDB.title_info(t) if t else None
            if info and info[0] != owner:
                return "Same data (file naam) pehle aa chuka hai"
            return None

        cleaned, removed, new_plain = strip_duplicate_links(text, check)
        plain_keys = {k for k, _ in new_plain}

        extra_new, extra_dup, seen_extra = [], [], set()
        for u in extra:
            k = link_key(u)
            if k in plain_keys or k in seen_extra:
                continue
            seen_extra.add(k)
            reason = check(k)
            if reason:
                extra_dup.append((u, reason))
            else:
                extra_new.append((k, u))

        if edited:                                     # edit me hataye gaye links DB se bhi hatao
            current = {link_key(u) for u in all_urls}
            gone = [k for k in LINKDB.owned_keys(owner) if k not in current]
            if gone:
                LINKDB.forget_keys(gone)

        total_dup = len(removed) + len(extra_dup)
        total_new = len(new_plain) + len(extra_new)
        all_dup = total_dup > 0 and total_new == 0

        if not all_dup and (new_plain or extra_new or edited):
            fresh = new_plain + extra_new
            items = [(k, owner) for k, _ in fresh]
            tlist = [(titles[k], owner, k) for k, _ in fresh if titles.get(k)]
            if items or tlist:
                LINKDB.save_links(items, tlist)

    return SimpleNamespace(text=text, cleaned=cleaned, removed=removed, extra_dup=extra_dup,
                           total_dup=total_dup, total_new=total_new, all_dup=all_dup,
                           extra_new=[u for _, u in extra_new])


# ============================================================
# FORMATTING TEXT (header/footer)
# ============================================================
ALLOWED_TAGS = ("b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
                "code", "pre", "blockquote", "tg-spoiler")
_TAG_RE = re.compile(r"&lt;(/?)(" + "|".join(re.escape(t) for t in ALLOWED_TAGS) + r")&gt;")
_A_OPEN_RE = re.compile(r'&lt;a\s+href=(["\'])(https?://[^"\'<>\s]+)\1\s*&gt;')
_URL_SPLIT_RE = re.compile(r"(https?://\S+)")
_TOKEN_RE = re.compile(r"<(/?)([a-z-]+)(?:\s[^>]*)?>")


FORMAT_ON_TEXT = (
    "🟢 FORMATTING ON\n"
    "Aap apni post me in tareeqon se formatting kar sakte hain:\n"
    "• Bold ke liye: **text**  ya  <b>text</b>\n"
    "• Italic ke liye: __text__  ya  <i>text</i>\n"
    "• Underline ke liye: <u>text</u>\n"
    "• Strikethrough ke liye: ~~text~~  ya  <s>text</s>\n"
    "• Spoiler ke liye: ||text||  ya  <tg-spoiler>text</tg-spoiler>\n"
    "• Code ke liye: `text`  ya  <code>text</code>\n"
    '• Link ke liye: <a href="https://link.com">Click Here</a>\n\n'
    "/Format_list\n"
    "/Remove_formate (FORMAT TEXT NO.)"
)


def format_enabled():
    return bot_data.get("format_status", True)


def _balanced(html_text):
    stack = []
    for m in _TOKEN_RE.finditer(html_text):
        closing, name = m.group(1) == "/", m.group(2)
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


FORMAT_ITEMS = [
    (1, "Bold", "**text**  ya  <b>text</b>", ("b", "strong")),
    (2, "Italic", "__text__  ya  <i>text</i>", ("i", "em")),
    (3, "Underline", "<u>text</u>", ("u", "ins")),
    (4, "Strikethrough", "~~text~~  ya  <s>text</s>", ("s", "strike", "del")),
    (5, "Spoiler", "||text||  ya  <tg-spoiler>text</tg-spoiler>", ("tg-spoiler",)),
    (6, "Code", "`text`  ya  <code>text</code>", ("code", "pre")),
    (7, "Link", '<a href="https://link.com">Click Here</a>', ()),
]
_A_FULL_RE = re.compile(r'&lt;a\s+href=(["\'])(https?://[^"\'<>\s]+)\1\s*&gt;(.*?)&lt;/a&gt;', re.S)
_MD_RULES = [  # (format no., regex, html tag)
    (1, re.compile(r"\*\*(.+?)\*\*", re.S), "b"),
    (2, re.compile(r"__(.+?)__", re.S), "i"),
    (4, re.compile(r"~~(.+?)~~", re.S), "s"),
    (5, re.compile(r"\|\|(.+?)\|\|", re.S), "tg-spoiler"),
    (6, re.compile(r"`([^`\n]+)`"), "code"),
]


def disabled_formats():
    with SETTINGS_LOCK:
        return {int(x) for x in (bot_data.get("disabled_formats") or []) if str(x).isdigit()}


def render_rich(text):
    """
    **bold**  __italic__  ~~strike~~  ||spoiler||  `code`  ya  <b> <i> <u> <s> <code>
    <blockquote> <tg-spoiler> <a href="https://..">link</a>. Galat formatting ho to plain text.
    /remove_format se band kiya gaya format sirf normal text ban jata hai (style nahi lagta).
    """
    dis = disabled_formats()
    off_tags = {t for n, _, _, tags in FORMAT_ITEMS if n in dis for t in tags}
    esc = htmlmod.escape(text, quote=False)
    if 7 in dis:                                   # link band: "text (url)" dikhega
        esc = _A_FULL_RE.sub(lambda m: f"{m.group(3)} ({m.group(2)})", esc)
    parts = _URL_SPLIT_RE.split(esc)
    for i in range(0, len(parts), 2):              # link ke andar markdown nahi lagta
        seg = parts[i]
        for n, rx, tag in _MD_RULES:
            seg = rx.sub(r"\1" if n in dis else rf"<{tag}>\1</{tag}>", seg)
        parts[i] = seg
    out = "".join(parts)
    out = _TAG_RE.sub(lambda m: "" if m.group(2) in off_tags else f"<{m.group(1)}{m.group(2)}>", out)
    out = _A_OPEN_RE.sub(lambda m: f'<a href="{m.group(2)}">', out)
    out = out.replace("&lt;/a&gt;", "</a>")
    if not _balanced(out):
        return htmlmod.escape(text, quote=False)
    return out


def html_to_plain(text):
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", text))


def build_final(body, code_prefix, limit):
    """Final post = [Post Code] + Header + Body + Footer. Return (text, parse_mode)."""
    header = bot_data.get("header") or ""
    footer = bot_data.get("footer") or ""
    if not format_enabled():
        final = body
        if header:
            final = f"{header}\n\n{final}" if final else header
        final = f"{code_prefix}{final}"
        if footer:
            final = f"{final}\n\n{footer}"
        return final[:limit], None

    head = htmlmod.escape(code_prefix, quote=False)
    if header:
        head += (render_rich(header) + "\n\n") if body else render_rich(header)
    tail = ("\n\n" + render_rich(footer)) if footer else ""
    body_html = htmlmod.escape(body, quote=False)
    avail = limit - len(head) - len(tail)
    if avail < 0:
        return html_to_plain(head + tail)[:limit], None
    if len(body_html) > avail:                    # lamba text: tag kate bina chhota karo
        cut = body_html[:max(avail - 1, 0)]
        amp = cut.rfind("&")
        if amp != -1 and ";" not in cut[amp:]:
            cut = cut[:amp]
        body_html = (cut + "…") if avail >= 1 else ""
    return head + body_html + tail, "HTML"


def _send_preview(message, text):
    if not format_enabled():
        return
    r = render_rich(text)
    if r == htmlmod.escape(text, quote=False):
        return
    try:
        bot.send_message(message.chat.id, "👁 Preview:\n\n" + r, parse_mode="HTML")
    except Exception:
        pass


# ============================================================
# AUTO MERGE: saari .json / .db link files  ->  merge_links.db
# ============================================================
class NotLinkData(Exception):
    """File sahi hai par usme link data nahi (settings, pending, etc.)."""


MERGE_LOCK = threading.Lock()
MERGE_BATCH = 5000
_NOT_LINK_JSON_KEYS = {"header", "footer", "channels", "regex_filters", "non_text_list", "admins",
                       "button_text", "my_channel_link"}


def _merge_skip(path):
    ap = os.path.abspath(path)
    base = os.path.basename(ap).lower()
    own = {os.path.abspath(x) for x in (DB_FILE, SETTINGS_FILE, JOURNAL_FILE, LOCK_FILE)}
    if ap in own or base.startswith(os.path.basename(DB_FILE).lower()):   # merge_links.db-journal bhi
        return True
    return base.endswith((".tmp", ".lock")) or not base.endswith(MERGE_EXT)


def _chat_export_urls(data):
    """Telegram Desktop 'Export chat history' JSON: {"messages": [...]} ya poore account ka {"chats": {"list": [...]}}.
    Message text, hyperlink (text_link) aur link entities se saare content links nikalta hai (t.me links nahi)."""
    if isinstance(data.get("messages"), list):
        chats = [data]
    else:
        chats = [c for c in data["chats"]["list"] if isinstance(c, dict)]
    urls = []
    for ch in chats:
        for m in ch.get("messages") or []:
            if not isinstance(m, dict):
                continue
            parts, txt = [], m.get("text")
            if isinstance(txt, str):
                parts.append(txt)
            elif isinstance(txt, list):
                for x in txt:
                    if isinstance(x, str):
                        parts.append(x)
                    elif isinstance(x, dict):
                        parts.append(str(x.get("href") or x.get("text") or ""))
            for e in m.get("text_entities") or []:
                if isinstance(e, dict):
                    if e.get("href"):
                        parts.append(str(e["href"]))
                    elif e.get("type") == "link":
                        parts.append(str(e.get("text", "")))
            for part in parts:
                for u in LEGACY_URL_RE.findall(part):
                    u = u.rstrip(TRAIL)
                    if u and is_content_link(u):
                        urls.append(u)
    return urls


def _parse_json_links(data):
    """Purana format: {"owners": {key: owner}, "links": [url..], "titles": {tkey: {owner, key}}, "codes": [..]}
    Ya sirf links ki list, ya {url/key: owner} map."""
    owners, titles, codes = {}, [], []

    def add(u, owner="legacy"):
        if isinstance(u, str) and u.strip():
            u = u.strip()
            k = link_key(u) if u.lower().startswith(("http://", "https://")) else u
            owners.setdefault(k, str(owner) if owner else "legacy")

    if isinstance(data, list):
        for u in data:
            add(u)
        return owners, titles, codes
    if not isinstance(data, dict):
        raise NotLinkData("link data nahi mila")
    if _NOT_LINK_JSON_KEYS & set(data) or ("posts" in data and "jobs" in data):
        raise NotLinkData("ye settings/pending file hai, link database nahi")
    if isinstance(data.get("messages"), list) or (
            isinstance(data.get("chats"), dict) and isinstance(data["chats"].get("list"), list)):
        for u in _chat_export_urls(data):          # Telegram chat export (ChatExport_*.json)
            add(u)
        return owners, titles, codes
    known = False
    o = data.get("owners")
    if isinstance(o, dict):
        known = True
        for k, v in o.items():
            add(str(k), v)
    l = data.get("links")
    if isinstance(l, (list, tuple)):
        known = True
        for u in l:
            add(u)
    elif isinstance(l, dict):
        known = True
        for k, v in l.items():
            add(k, v if isinstance(v, str) else "legacy")
    t = data.get("titles")
    if isinstance(t, dict):
        known = True
        for tk, i in t.items():
            if isinstance(i, dict):
                titles.append((str(tk), str(i.get("owner", "")), str(i.get("key", ""))))
    c = data.get("codes")
    if isinstance(c, (list, tuple)):
        known = True
        codes = [str(x) for x in c]
    if not known:
        if data and all(isinstance(v, str) for v in data.values()) and all(("." in k or ":" in k) for k in data):
            for k, v in data.items():
                add(k, v)
        else:
            raise NotLinkData("link data nahi mila")
    return owners, titles, codes


def _merge_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    owners, titles, codes = _parse_json_links(data)
    items = list(owners.items())
    for i in range(0, max(len(items), 1), MERGE_BATCH):
        LINKDB.import_chunk(items[i:i + MERGE_BATCH], [], [])
    for i in range(0, len(titles), MERGE_BATCH):
        LINKDB.import_chunk([], titles[i:i + MERGE_BATCH], [])
    for i in range(0, len(codes), MERGE_BATCH):
        LINKDB.import_chunk([], [], codes[i:i + MERGE_BATCH])
    return len(items), len(titles), len(codes)


def _merge_sqlite(path):
    from pathlib import Path
    src = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        tabs = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not ({"owners", "codes", "titles"} & tabs):
            raise NotLinkData("link data nahi mila (owners/titles/codes table nahi)")
        counts = [0, 0, 0]
        for idx, (tab, sql) in enumerate((("owners", "SELECT key, owner FROM owners"),
                                          ("titles", "SELECT tkey, owner, key FROM titles"),
                                          ("codes", "SELECT code FROM codes"))):
            if tab not in tabs:
                continue
            cur = src.execute(sql)
            while True:
                rows = cur.fetchmany(MERGE_BATCH)
                if not rows:
                    break
                if idx == 0:
                    LINKDB.import_chunk([(str(a), str(b) if b else "legacy") for a, b in rows], [], [])
                elif idx == 1:
                    LINKDB.import_chunk([], [(str(a), str(b), str(c)) for a, b, c in rows], [])
                else:
                    LINKDB.import_chunk([], [], [str(r[0]) for r in rows])
                counts[idx] += len(rows)
        return tuple(counts)
    finally:
        src.close()


def merge_all_sources(force=False):
    """Folder ki saari .json/.db link files ko merge_links.db me merge karo.
    Original files ko chhua nahi jata. Jo file pehle merge ho chuki aur badli nahi use skip karte hain
    (force=True par sabko dobara, safe: duplicate nahi banta)."""
    lines = []
    with MERGE_LOCK:
        b_links, b_codes = LINKDB.count_links(), LINKDB.count_codes()
        for d in MERGE_DIRS:
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if not os.path.isfile(path) or _merge_skip(path):
                    continue
                try:
                    st = os.stat(path)
                    sig = f"v2|{name}|{st.st_size}|{int(st.st_mtime)}"      # v2: chat-export support aane ke baad sab files ek baar dobara padhi jayengi
                    if not force and LINKDB.is_merged(sig):
                        continue
                    if name.lower().endswith((".db", ".sqlite")):
                        n = _merge_sqlite(path)
                    else:
                        n = _merge_json(path)
                    LINKDB.mark_merged(sig, name)
                    lines.append(f"✅ {name}: {n[0]} links, {n[1]} titles, {n[2]} codes")
                except NotLinkData as e:
                    try:
                        LINKDB.mark_merged(sig, name)
                    except Exception:
                        pass
                    lines.append(f"⏭ {name}: skip ({e})")
                except Exception as e:
                    print(f"Merge error ({name}): {e}")
                    lines.append(f"❌ {name}: merge nahi hua ({e})")
        a_links, a_codes = LINKDB.count_links(), LINKDB.count_codes()
    lines.append("")
    lines.append(f"📚 {os.path.basename(DB_FILE)}: {a_links} links (+{a_links - b_links}), "
                 f"{a_codes} codes (+{a_codes - b_codes})")
    return lines


@bot.message_handler(commands=['merge_now'])
def merge_now_cmd(message):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        bot.reply_to(message, "⛔ Ye command sirf owner/admin chala sakta hai.")
        return
    force = _plain_arg(message).lower() == "all"
    bot.reply_to(message, "🔄 Merge chalu... saari .json/.db files scan ho rahi hain.")

    def job():
        lines = merge_all_sources(force)
        notify_owner_to(message.chat.id, "🗃 MERGE REPORT\n\n" + "\n".join(lines))
    POST_POOL.submit(_run_safe, job)


def notify_owner_to(chat_id, text):
    enqueue_send(chat_id, partial(bot.send_message, chat_id, text[:4000], disable_web_page_preview=True))


# ============================================================
# FILTERS: NON-TEXT + REGEX/WILDCARD
# ============================================================
REGEX_MAX_LEN = 200
_RISKY_RX = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")     # (a+)+ jaise bhaari pattern
_RX_CACHE = {}


def _norm_text(t):
    return " ".join((t or "").split()).casefold()


def wildcard_to_regex(p):
    out = []
    for ch in p:
        out.append(".*?" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return "".join(out)


def _compile_filter(kind, pattern):
    key = (kind, pattern)
    rx = _RX_CACHE.get(key)
    if rx is None:
        src = wildcard_to_regex(pattern) if kind == "wild" else pattern
        rx = re.compile(src, re.IGNORECASE | re.DOTALL)
        if len(_RX_CACHE) > 500:
            _RX_CACHE.clear()
        _RX_CACHE[key] = rx
    return rx


def matches_regex_filter(text):
    with SETTINGS_LOCK:
        items = list(bot_data.get("regex_filters") or [])
    if not items or not text:
        return False
    sample = text[:6000]
    collapsed = " ".join(sample.split())
    for it in items:
        try:
            rx = _compile_filter(it.get("t"), it.get("p", ""))
        except re.error:
            continue
        if rx.search(sample) or rx.search(collapsed):
            return True
    return False


def contains_blocked_text(text):
    if not text:
        return False
    body = _norm_text(text)
    with SETTINGS_LOCK:
        items = list(bot_data.get("non_text_list") or [])
    for b in items:
        nb = _norm_text(b)
        if nb and nb in body:
            return True
    return matches_regex_filter(text)


# ============================================================
# ACCESS CONTROL
# ============================================================
def is_admin(uid):
    if uid is None:
        return False
    if uid == OWNER_ID:
        return True
    with SETTINGS_LOCK:
        return uid in (bot_data.get("admins") or [])


def owner_only(func):
    """Owner ya jodhe gaye admin chala sakte hain."""
    @wraps(func)
    def wrapper(message):
        uid = message.from_user.id if message.from_user else None
        if not is_admin(uid):
            try:
                bot.reply_to(message, "⛔ Ye command sirf owner/admin chala sakta hai.")
            except Exception:
                pass
            return
        return func(message)
    return wrapper


def super_owner_only(func):
    @wraps(func)
    def wrapper(message):
        uid = message.from_user.id if message.from_user else None
        if uid != OWNER_ID:
            try:
                bot.reply_to(message, "⛔ Ye command sirf main owner chala sakta hai.")
            except Exception:
                pass
            return
        return func(message)
    return wrapper


def _plain_arg(message):
    parts = (message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _command_arg(message):
    """Command ke baad ka text; na ho to replied message ka text."""
    arg = _plain_arg(message)
    if not arg and message.reply_to_message:
        r = message.reply_to_message
        arg = (r.text or r.caption or "").strip()
    return arg


# ============================================================
# COMMANDS
# ============================================================
HELP_TEXT = (
    f"🤖 {BOT_NAME}\n\n"
    "📌 HEADER / FOOTER / BUTTON\n"
    "/add_header [text] - Header set\n"
    "/remove_header - Header hatao\n"
    "/add_footer [text] - Footer set\n"
    "/remove_footer - Footer hatao\n"
    "/set_button Name | https://link - Inline button\n"
    "/remove_button - Button hatao\n"
    "/format_on | /format_off - Bold/italic formatting\n"
    "/format_list - Formatting ki list (number ke saath)\n"
    "/remove_format [no.] - Koi format hatao (jaise /remove_format 3)\n"
    "   Formatting: **bold** __italic__ ~~strike~~ ||spoiler|| `code`\n"
    "   ya <b> <i> <u> <s> <code> <a href=\"https://..\">link</a>\n\n"
    "🔑 UNIQUE POST CODE\n"
    "/uniq_code_on | /uniq_code_off\n\n"
    "🔗 LINK REPLACE\n"
    "/set_mylink [link] - Aapka channel link\n"
    "/link_replace_on | /link_replace_off\n\n"
    "🔔 3 ALERT SYSTEM (ON/OFF)\n"
    "♻️ Replace alert:\n"
    "/replace_notification_on\n"
    "/replace_notification_off\n"
    "🚫 Duplicate alert:\n"
    "/duplicate_notification_on\n"
    "/duplicate_notification_off\n"
    "🧹 Clear alert:\n"
    "/clear_notification_on\n"
    "/clear_notification_off\n"
    "(Bina on/off likhne par status dikhega)\n\n"
    "📝 SURROUNDING TEXT\n"
    "/enable_text (ya /anable_text) - Links ke saath baaki text bhi jayega (default ON)\n"
    "/disable_text - Sirf links jayenge, aas-paas ka text hat jayega\n\n"
    "📢 CHANNELS (auto-forward)\n"
    "/add_channel [@user ya -100id]\n"
    "/channel_list\n"
    "/remove_channel [number]\n\n"
    "🚫 FILTERS\n"
    "/non_text [text] - Aate hi delete\n"
    "/non_text_list | /remove_non_text [number/all]\n"
    "/regex_add [pattern] | /wild_add [pattern]\n"
    "/regex_list | /regex_remove [number/all]\n\n"
    "👑 ADMIN (sirf owner)\n"
    "/add_admin [id] | /remove_admin [id] | /admin_list\n\n"
    "🗃 /merge_now - Saari .json/.db files merge_links.db me merge\n"
    "   (/merge_now all = sabko dobara merge)\n"
    "📥 /download_links - Saare saved links .txt file me is chat/group me bhejo (owner/admin)\n"
    "🗄 /clear_links - Saved links database saaf (sirf owner)\n"
    "📊 /status - Live status (/status reset)"
)


@bot.message_handler(commands=['start'])
def send_welcome(message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📢 Join Official Channel", url=OFFICIAL_CHANNEL))
    bot.reply_to(message, f"👋 Namaste! Main {BOT_NAME} hoon.\n\n"
                          "Mujhe link wali post bhejo, main duplicate links hata dunga.\n"
                          "Commands ki list: /help\n\n"
                          "🔥 Latest updates, premium content aur naye bots ke liye "
                          f"hamara Official Channel join karo 👇\n{OFFICIAL_CHANNEL}",
                 reply_markup=kb, disable_web_page_preview=True)


@bot.message_handler(commands=['help'])
def send_help(message):
    bot.reply_to(message, HELP_TEXT + "\n\n📝 Surrounding text abhi: "
                 + ("🟢 ON" if surrounding_text_enabled() else "🔴 OFF"))


# ---------- header / footer / button ----------
def _set_text_cmd(message, key, label, cmds):
    text = message.text or ""
    for c in cmds:
        text = text.replace(c, "", 1) if text.startswith(c) else text
    text = text.strip()
    if not text:
        bot.reply_to(message, f"⚠️ Sahi format: {cmds[0]} [{label} Text]")
        return
    bot_data[key] = text
    save_settings()
    bot.reply_to(message, f"✅ {label} set:\n\n{text}")
    _send_preview(message, text)


@bot.message_handler(commands=['add_header', 'addheader'])
@owner_only
def add_header(message):
    parts = (message.text or "").split(None, 1)
    _set_text_cmd(message, "header", "Header", [parts[0]])


@bot.message_handler(commands=['add_footer', 'addfooter'])
@owner_only
def add_footer(message):
    parts = (message.text or "").split(None, 1)
    _set_text_cmd(message, "footer", "Footer", [parts[0]])


@bot.message_handler(commands=['remove_header', 'removeheader'])
@owner_only
def remove_header(message):
    bot_data["header"] = ""
    save_settings()
    bot.reply_to(message, "🗑️ Header hata diya gaya hai!")


@bot.message_handler(commands=['remove_footer', 'removefooter'])
@owner_only
def remove_footer(message):
    bot_data["footer"] = ""
    save_settings()
    bot.reply_to(message, "🗑️ Footer hata diya gaya hai!")


@bot.message_handler(commands=['set_button', 'setbutton'])
@owner_only
def set_button(message):
    parts = _plain_arg(message).split('|')
    if len(parts) == 2 and parts[0].strip() and parts[1].strip().startswith("http"):
        bot_data["button_text"] = parts[0].strip()
        bot_data["button_url"] = parts[1].strip()
        save_settings()
        bot.reply_to(message, f"✅ Button updated!\nText: {bot_data['button_text']}\nURL: {bot_data['button_url']}")
    else:
        bot.reply_to(message, "⚠️ Sahi format: /set_button Name | https://link.com")


@bot.message_handler(commands=['remove_button', 'removebutton'])
@owner_only
def remove_button(message):
    bot_data["button_text"] = ""
    bot_data["button_url"] = ""
    save_settings()
    bot.reply_to(message, "🗑️ Button hata diya gaya hai!")


FORMAT_ON_TEXT = (
    "🟢 FORMATTING ON\n"
    "Aap apni post me in tareeqon se formatting kar sakte hain:\n"
    "• Bold ke liye: **text**  ya  <b>text</b>\n"
    "• Italic ke liye: __text__  ya  <i>text</i>\n"
    "• Underline ke liye: <u>text</u>\n"
    "• Strikethrough ke liye: ~~text~~  ya  <s>text</s>\n"
    "• Spoiler ke liye: ||text||  ya  <tg-spoiler>text</tg-spoiler>\n"
    "• Code ke liye: `text`  ya  <code>text</code>\n"
    "• Link ke liye: <a href=\"https://link.com\">Click Here</a>"
)


@bot.message_handler(commands=['format_on', 'format_off'])
@owner_only
def format_toggle(message):
    on = message.text.split()[0].lstrip('/').split('@')[0].lower() == "format_on"
    bot_data["format_status"] = on
    if on:
        bot_data["disabled_formats"] = []          # /format_on se hataye gaye sab format wapas
    save_settings()
    bot.reply_to(message, FORMAT_ON_TEXT if on
                 else "🔴 FORMATTING OFF (header/footer plain text me jayega)")


def _format_list_text():
    dis = disabled_formats()
    head = "📋 FORMAT LIST" if format_enabled() else "📋 FORMAT LIST\n🔴 Formatting abhi OFF hai (/format_on se chalu karo)"
    lines = [head, ""]
    for n, name, how, _ in FORMAT_ITEMS:
        lines.append(f"{n}. {name} - {how}  " + ("🔴 REMOVED" if n in dis else "🟢 ON"))
    lines += ["", "Koi format hatane ke liye: /remove_format [no.]   (jaise /remove_format 3)",
              "Kai ek saath: /remove_format 3 5   |   Sab: /remove_format all",
              "Sab wapas lane ke liye: /format_on"]
    return "\n".join(lines)


@bot.message_handler(commands=['format_list', 'formatlist'])
@owner_only
def format_list_cmd(message):
    bot.reply_to(message, _format_list_text())


@bot.message_handler(commands=['remove_format', 'remove_formate', 'removeformat'])
@owner_only
def remove_format_cmd(message):
    args = _plain_arg(message).lower().replace(",", " ").split()
    if not args:
        bot.reply_to(message, "⚠️ Format: /remove_format [no.]\n\n" + _format_list_text())
        return
    names = {n: name for n, name, _, _ in FORMAT_ITEMS}
    if args == ["all"]:
        nums = list(names)
    else:
        if not all(a.isdigit() and int(a) in names for a in args):
            bot.reply_to(message, "⚠️ Galat number. 1 se 7 tak ka number likho, ya all.\n\n" + _format_list_text())
            return
        nums = sorted({int(a) for a in args})
    with SETTINGS_LOCK:
        cur = {int(x) for x in (bot_data.get("disabled_formats") or []) if str(x).isdigit()}
        new = [n for n in nums if n not in cur]
        bot_data["disabled_formats"] = sorted(cur | set(nums))
    save_settings()
    if not new:
        bot.reply_to(message, "⚠️ Ye format pehle se hata hua hai.\n\n" + _format_list_text())
        return
    gone = "\n".join(f"🗑️ {n}. {names[n]}" for n in new)
    bot.reply_to(message, f"✅ Format hata diya:\n{gone}\n\n"
                          "Ab ye style header/footer me nahi lagega, text normal dikhega.\n"
                          "Wapas lane ke liye: /format_on")


# ---------- unique code / link replace ----------
@bot.message_handler(commands=['uniq_code_on', 'uniq_code_off'])
@owner_only
def uniq_code_toggle(message):
    on = message.text.split()[0].lstrip('/').split('@')[0].lower() == "uniq_code_on"
    bot_data["unique_code_status"] = on
    save_settings()
    bot.reply_to(message, "🟢 UNIQUE POST CODE ON" if on else "🔴 UNIQUE POST CODE OFF")


@bot.message_handler(commands=['link_replace_on', 'link_replace_off'])
@owner_only
def link_replace_toggle(message):
    on = message.text.split()[0].lstrip('/').split('@')[0].lower() == "link_replace_on"
    if on and not bot_data.get("my_channel_link"):
        bot.reply_to(message, "⚠️ Pehle /set_mylink https://t.me/yourlink se apna link set karo.")
        return
    bot_data["link_replace_status"] = on
    save_settings()
    bot.reply_to(message, "🟢 LINK REPLACE ON" if on else "🔴 LINK REPLACE OFF")


@bot.message_handler(commands=['set_mylink', 'setmylink'])
@owner_only
def set_mylink(message):
    link = _plain_arg(message)
    if link.startswith("http"):
        bot_data["my_channel_link"] = link
        save_settings()
        bot.reply_to(message, f"✅ Replacement Link set ho gaya:\n{link}")
    else:
        bot.reply_to(message, "⚠️ Sahi format: /set_mylink https://t.me/yourlink")


# ---------- surrounding text ON/OFF ----------
def surrounding_text_enabled():
    return bot_data.get("surrounding_text_status", True)


def _hidden_tme_links(message):
    """Hyperlink (text_link) me chhupe t.me/telegram links (ye content-link nahi gine jaate)."""
    urls = []
    for ents in (message.entities, message.caption_entities):
        for e in ents or []:
            if e.type == "text_link" and e.url and not is_content_link(e.url):
                urls.append(e.url)
    return urls


def build_links_only(message, r):
    """Surrounding text OFF: duplicate hatane ke baad bache saare links (text + hyperlink/button) ek-ek line me."""
    body = r.cleaned if r.removed else r.text
    urls = [u.rstrip(TRAIL) for u in URL_RE.findall(body)]
    urls += list(r.extra_new)
    urls += _hidden_tme_links(message)
    out, seen = [], set()
    for u in urls:
        if not u:
            continue
        k = link_key(u)
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out


@bot.message_handler(commands=['enable_text', 'anable_text'])
@owner_only
def enable_text_cmd(message):
    bot_data["surrounding_text_status"] = True
    save_settings()
    bot.reply_to(message, "🟢 SURROUNDING TEXT ON (ab post me links ke saath baaki text bhi jayega)")


@bot.message_handler(commands=['disable_text'])
@owner_only
def disable_text_cmd(message):
    bot_data["surrounding_text_status"] = False
    save_settings()
    bot.reply_to(message, "🔴 SURROUNDING TEXT OFF (ab post me se aas-paas ka text hat kar sirf links hi jayenge)")


# ---------- 3 alert ON/OFF commands ----------
ALERT_SETTINGS = {
    "replace_notification": ("alert_replace", "Replace link alert (♻️)"),
    "duplicate_notification": ("alert_duplicate", "Duplicate links alert (🚫)"),
    "clear_notification": ("alert_clear", "Clear links alert (🧹)"),
}


@bot.message_handler(commands=[
    'replace_notification', 'replace_notification_on', 'replace_notification_off',
    'duplicate_notification', 'duplicate_notification_on', 'duplicate_notification_off',
    'clear_notification', 'clear_notification_on', 'clear_notification_off'])
@owner_only
def alert_toggle(message):
    parts = (message.text or "").split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    want = None
    if cmd.endswith("_on"):
        cmd, want = cmd[:-3], True
    elif cmd.endswith("_off"):
        cmd, want = cmd[:-4], False
    key, label = ALERT_SETTINGS[cmd]
    if want is None and len(parts) > 1:
        a = parts[1].lower()
        if a in ("on", "1", "yes", "chalu"):
            want = True
        elif a in ("off", "0", "no", "band"):
            want = False
    if want is None:
        now = "🟢 ON" if alert_on(key) else "🔴 OFF"
        bot.reply_to(message, f"{label}: {now}\n\nBadalne ke liye:\n/{cmd}_on\n/{cmd}_off")
        return
    bot_data[key] = want
    save_settings()
    if want:
        bot.reply_to(message, f"🟢 {label} ON\nAb ye alert inline button me aayega.")
    else:
        bot.reply_to(message, f"🔴 {label} OFF\nKaam pehle jaisa hoga, bas ye alert nahi aayega.")


# ---------- channels ----------
@bot.message_handler(commands=['add_channel', 'addchannel'])
@owner_only
def add_channel(message):
    ch = _plain_arg(message)
    if ch.startswith('@') or ch.startswith('-'):
        if ch.startswith('-'):
            try:
                ch = int(ch)
            except ValueError:
                pass
        if ch not in bot_data["channels"]:
            bot_data["channels"].append(ch)
            save_settings()
            bot.reply_to(message, f"✅ Channel add ho gaya: {ch}")
        else:
            bot.reply_to(message, "⚠️ Ye channel pehle se added hai.")
    else:
        bot.reply_to(message, "⚠️ Sahi format:\n/add_channel @yourchannel\n/add_channel -100xxxxxxxxxx")


@bot.message_handler(commands=['channel_list', 'channellist'])
@owner_only
def list_channels(message):
    chs = bot_data.get("channels", [])
    if not chs:
        bot.reply_to(message, "⚠️ List me koi channel nahi hai.")
        return
    bot.reply_to(message, "📋 Channels:\n\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(chs, 1)))


@bot.message_handler(commands=['remove_channel', 'removechannel'])
@owner_only
def remove_channel(message):
    chs = bot_data.get("channels", [])
    arg = _plain_arg(message)
    if not arg.isdigit() or not (1 <= int(arg) <= len(chs)):
        bot.reply_to(message, "⚠️ Format: /remove_channel [number]\nList: /channel_list")
        return
    removed = chs.pop(int(arg) - 1)
    save_settings()
    bot.reply_to(message, f"🗑️ Channel hata diya: {removed}")


# ---------- non_text + regex/wildcard ----------
@bot.message_handler(commands=['non_text', 'add_non_text'])
@owner_only
def add_non_text(message):
    text = _command_arg(message)
    if not text:
        bot.reply_to(message, "⚠️ Format: /non_text [text]\nYa kisi message par reply karke /non_text likho.")
        return
    with SETTINGS_LOCK:
        items = bot_data.setdefault("non_text_list", [])
        n = _norm_text(text)
        dup = next((i for i, x in enumerate(items, 1) if _norm_text(x) == n), None)
        if dup is None:
            items.append(text)
            save_settings()
            num = len(items)
    if dup is not None:
        bot.reply_to(message, f"⚠️ Ye text pehle se list me hai (No. {dup}).")
    else:
        bot.reply_to(message, f"✅ Non-text add hua (No. {num}). Ye text aate hi delete hoga.")


def _list_msg(items, title, fmt=lambda x: str(x)):
    if not items:
        return f"⚠️ {title}: list khali hai."
    return f"📋 {title}:\n\n" + "\n".join(f"{i}. {fmt(x)}" for i, x in enumerate(items, 1))


def _remove_from(message, key, label):
    arg = _plain_arg(message).lower()
    with SETTINGS_LOCK:
        items = bot_data.setdefault(key, [])
        if arg == "all":
            n = len(items)
            items.clear()
            save_settings()
            bot.reply_to(message, f"🗑️ {n} {label} hata diye.")
        elif arg.isdigit() and 1 <= int(arg) <= len(items):
            gone = items.pop(int(arg) - 1)
            save_settings()
            bot.reply_to(message, f"🗑️ Hata diya: {gone if isinstance(gone, str) else gone.get('p')}")
        else:
            bot.reply_to(message, "⚠️ Number ya all likho. Example: 1  ya  all")


@bot.message_handler(commands=['non_text_list'])
@owner_only
def list_non_text(message):
    bot.reply_to(message, _list_msg(bot_data.get("non_text_list") or [], "Non-text list"))


@bot.message_handler(commands=['remove_non_text'])
@owner_only
def remove_non_text(message):
    _remove_from(message, "non_text_list", "non-text")


def _add_filter(message, kind):
    pattern = _plain_arg(message)
    label = "Regex" if kind == "re" else "Wildcard"
    if not pattern:
        bot.reply_to(message, f"⚠️ Format: /{'regex_add' if kind == 're' else 'wild_add'} [pattern]")
        return
    if len(pattern) > REGEX_MAX_LEN:
        bot.reply_to(message, f"⚠️ Pattern bahut lamba hai (max {REGEX_MAX_LEN} chars).")
        return
    if kind == "re" and _RISKY_RX.search(pattern):
        bot.reply_to(message, "⚠️ Ye pattern bot ko slow/hang kar sakta hai (nested + / *). Saral pattern likhein.")
        return
    try:
        rx = _compile_filter(kind, pattern)
    except re.error as e:
        bot.reply_to(message, f"⚠️ Galat pattern: {e}")
        return
    if rx.search(""):
        bot.reply_to(message, "⚠️ Ye pattern khali text par bhi match hota hai. Aur specific likhein.")
        return
    with SETTINGS_LOCK:
        items = bot_data.setdefault("regex_filters", [])
        if any(x.get("t") == kind and x.get("p") == pattern for x in items):
            bot.reply_to(message, "⚠️ Ye filter pehle se hai.")
            return
        items.append({"t": kind, "p": pattern})
        save_settings()
        n = len(items)
    bot.reply_to(message, f"✅ {label} filter add hua (No. {n}). Match hone par post delete hogi.")


@bot.message_handler(commands=['regex_add'])
@owner_only
def regex_add(message):
    _add_filter(message, "re")


@bot.message_handler(commands=['wild_add'])
@owner_only
def wild_add(message):
    _add_filter(message, "wild")


@bot.message_handler(commands=['regex_list'])
@owner_only
def regex_list(message):
    bot.reply_to(message, _list_msg(bot_data.get("regex_filters") or [], "Regex/Wildcard filters",
                                    lambda x: f"[{'REGEX' if x.get('t') == 're' else 'WILD'}] {x.get('p')}"))


@bot.message_handler(commands=['regex_remove'])
@owner_only
def regex_remove(message):
    _remove_from(message, "regex_filters", "filter")


# ---------- admins ----------
def _target_user_id(message):
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
        return int(parts[1])
    r = message.reply_to_message
    if r and r.from_user:
        return r.from_user.id
    return None


@bot.message_handler(commands=['add_admin'])
@super_owner_only
def add_admin(message):
    uid = _target_user_id(message)
    if uid is None:
        bot.reply_to(message, "⚠️ Format: /add_admin [user id]  ya kisi ke message par reply karke.")
        return
    with SETTINGS_LOCK:
        admins = bot_data.setdefault("admins", [])
        if uid == OWNER_ID or uid in admins:
            bot.reply_to(message, "⚠️ Ye pehle se owner/admin hai.")
            return
        admins.append(uid)
        save_settings()
    bot.reply_to(message, f"✅ Admin add hua: {uid}")


@bot.message_handler(commands=['remove_admin'])
@super_owner_only
def remove_admin(message):
    uid = _target_user_id(message)
    with SETTINGS_LOCK:
        admins = bot_data.setdefault("admins", [])
        if uid is None or uid not in admins:
            bot.reply_to(message, "⚠️ Ye user admin list me nahi hai. Format: /remove_admin [user id]")
            return
        admins.remove(uid)
        save_settings()
    bot.reply_to(message, f"🗑️ Admin hata diya: {uid}")


@bot.message_handler(commands=['admin_list'])
@super_owner_only
def admin_list(message):
    admins = bot_data.get("admins") or []
    lines = [f"👑 Owner: {OWNER_ID}"] + [f"{i}. {a}" for i, a in enumerate(admins, 1)]
    bot.reply_to(message, "\n".join(lines) if admins else lines[0] + "\n\nExtra admin koi nahi.")


@bot.message_handler(commands=['clear_links'])
@super_owner_only
def clear_links(message):
    n = LINKDB.count_links()
    if _plain_arg(message).lower() != "confirm":
        bot.reply_to(message, f"⚠️ {n} saved links ka database saaf hoga (duplicate memory reset).\n"
                              "Pakka karne ke liye: /clear_links confirm")
        return
    LINKDB.clear_all()
    bot.reply_to(message, f"🧹 {n} links database se hata diye.")


# ---------- download all saved links (group/chat me .txt file) ----------
DOWNLOAD_CHUNK = 200000          # ek file me max itne links (Telegram 50MB limit se bahut neeche)


def _key_to_url(key):
    """DB me link 'key' form me save hota hai (https/www hata hua); download ke liye wapas URL banao."""
    k = (key or "").strip()
    if k.lower().startswith(("http://", "https://")):
        return k
    if k.startswith("terabox:"):
        return "https://terabox.com/s/1" + k[len("terabox:"):]
    if re.match(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+([/?:#]|$)", k):
        return "https://" + k
    return k


@bot.message_handler(commands=['download_links', 'downloadlinks', 'all_links'])
@owner_only
def download_links_cmd(message):
    chat_id = message.chat.id
    total = LINKDB.count_links()
    if total == 0:
        bot.reply_to(message, "⚠️ Database me abhi koi link saved nahi hai.")
        return
    bot.reply_to(message, f"⏳ {total} saved links ki file ban rahi hai... thodi der me is chat me aayegi.")

    def job():
        import io
        keys = [r[0] for r in LINKDB._q("SELECT key FROM owners ORDER BY rowid")]
        urls = [_key_to_url(k) for k in keys if k]
        parts = [urls[i:i + DOWNLOAD_CHUNK] for i in range(0, len(urls), DOWNLOAD_CHUNK)]
        stamp = time.strftime("%Y-%m-%d_%H-%M")
        for n, chunk in enumerate(parts, 1):
            data = ("\n".join(chunk) + "\n").encode("utf-8")
            fname = (f"all_links_{stamp}.txt" if len(parts) == 1
                     else f"all_links_{stamp}_part{n}of{len(parts)}.txt")
            cap = f"📥 All saved links: {len(chunk)}" + (f" (part {n}/{len(parts)}, total {len(urls)})" if len(parts) > 1 else "")

            def send(data=data, fname=fname, cap=cap):
                bio = io.BytesIO(data)
                bio.name = fname
                bot.send_document(chat_id, bio, caption=cap)
            send_now(chat_id, send)

        # 🧠 Bot-memory file (.json): naye account me bot ko ye links yaad dilane ke liye (/merge_now isi ko padhta hai)
        rows = LINKDB._q("SELECT key, owner FROM owners ORDER BY rowid")
        titles = LINKDB._q("SELECT tkey, owner, key FROM titles")
        codes = [r[0] for r in LINKDB._q("SELECT code FROM codes")]
        jparts = [rows[i:i + DOWNLOAD_CHUNK] for i in range(0, len(rows), DOWNLOAD_CHUNK)] or [[]]
        for n, chunk in enumerate(jparts, 1):
            payload = {"owners": {k: o for k, o in chunk}}
            if n == len(jparts):
                payload["titles"] = {t: {"owner": o, "key": k} for t, o, k in titles}
                payload["codes"] = codes
            jdata = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            jname = (f"bot_memory_{stamp}.json" if len(jparts) == 1
                     else f"bot_memory_{stamp}_part{n}of{len(jparts)}.json")
            jcap = "🧠 Bot memory file: naye account ke bot folder me rakho, phir /merge_now chalao"

            def send_json(jdata=jdata, jname=jname, jcap=jcap):
                bio = io.BytesIO(jdata)
                bio.name = jname
                bot.send_document(chat_id, bio, caption=jcap)
            send_now(chat_id, send_json)
    POST_POOL.submit(_run_safe, job)


# ---------- status ----------
@bot.message_handler(commands=['status'])
@owner_only
def status_cmd(message):
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].lower() == "reset":
        with STATS_LOCK:
            for k in STATS:
                STATS[k] = 0
            for k in TIMING:
                TIMING[k] = [0.0, 0]
        bot.reply_to(message, "🔄 Status counters zero kar diye.")
        return
    with STATS_LOCK:
        st = dict(STATS)
    with SEND_LOCK:
        pend = {k: q.qsize() for k, q in SEND_QUEUES.items()}
    try:
        waiting = POST_POOL._work_queue.qsize()
    except Exception:
        waiting = 0
    up = int(time.time() - START_TIME)
    onoff = lambda b: "ON" if b else "OFF"
    lines = [
        f"📊 {BOT_NAME} - Status", "",
        f"⏱ Uptime: {up // 3600}h {up % 3600 // 60}m",
        f"Mili hui posts: {st['received']}",
        f"Reply gaye: {st['replied']}",
        f"Channel me gaye: {st['channel_sent']}",
        f"Duplicate post block: {st['dup_blocked']}",
        f"Duplicate links hataye: {st['links_removed']}",
        f"Non-text delete: {st['non_text_deleted']}",
        f"Send fail (dropped): {st['dropped']}",
        f"Process fail: {st['failed']}",
        f"Restart ke baad dobara chalayi: {st['restored']}", "",
        f"Process intezaar me: {waiting}",
        f"Avg process time: {_avg('proc')}",
        f"Avg link-check time: {_avg('fetch')}",
        f"Pending journal: {len(JOURNAL['posts'])} posts, {len(JOURNAL['jobs'])} bhejna baaki",
        f"Links DB: {LINKDB.count_links()} links saved", "",
        f"Alerts: replace {onoff(alert_on('alert_replace'))} | duplicate {onoff(alert_on('alert_duplicate'))}"
        f" | clear {onoff(alert_on('alert_clear'))}",
        f"Unique code: {onoff(bot_data.get('unique_code_status'))} | Link replace: "
        f"{onoff(bot_data.get('link_replace_status'))} | Format: {onoff(format_enabled())}",
        f"Surrounding text: {onoff(surrounding_text_enabled())}",
        f"Channels: {len(bot_data.get('channels') or [])} | Filters: "
        f"{len(bot_data.get('regex_filters') or []) + len(bot_data.get('non_text_list') or [])} | "
        f"Admins: {len(bot_data.get('admins') or [])}",
        "Send queue:",
    ]
    if pend:
        lines += [f"• {k}: {v}" + (f" (gap {GAP_NOW[k]:.1f}s)" if k in GAP_NOW else "") for k, v in pend.items()]
    else:
        lines.append("• khali")
    lines += ["", "Counters zero karne ke liye: /status reset"]
    bot.reply_to(message, "\n".join(lines))


# ============================================================
# CHANNEL POSTS (channel me aayi post se duplicate hatao)
# ============================================================
CHANNEL_TYPES = ['text', 'photo', 'video', 'document', 'animation', 'audio']


def handle_channel_post(message, edited=False):
    text = message.text or message.caption or ""
    if contains_blocked_text(text):
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        return
    if not text and not extract_extra_links(message):
        return

    chat_id, mid = message.chat.id, message.message_id
    r = analyze(message, f"{chat_id}:{mid}", edited)
    if r.total_dup == 0:
        return

    title = message.chat.title or str(chat_id)
    try:
        if r.all_dup:
            bot.delete_message(chat_id, mid)
            action = "📝 Post me sirf duplicate links the, isliye poori post delete ki."
        elif r.removed:
            if message.content_type == 'text':
                bot.edit_message_text(r.cleaned, chat_id, mid, reply_markup=message.reply_markup,
                                      disable_web_page_preview=True)
            else:
                bot.edit_message_caption(caption=r.cleaned, chat_id=chat_id, message_id=mid,
                                         reply_markup=message.reply_markup)
            action = "✂️ Post se sirf duplicate links hatayi, baaki post rakhi."
            if r.extra_dup:
                action += "\n⚠️ Hyperlink/button me chhupe duplicate links automatically nahi hat sakte."
        else:
            action = "⚠️ Duplicate link hyperlink/button me chhupa hai, isliye post rakhi gayi. Manually check karein."
        _stat("links_removed", len(r.removed))
    except Exception as e:
        action = (f"⚠️ Post me badlav nahi ho paya: {e}\n"
                  "Bot ko channel me 'Delete messages' aur 'Edit messages' ka admin right do.")

    report = ["🗑️ CHANNEL ME DUPLICATE MILA" + (" (edited post)" if edited else ""), "",
              f"📢 Channel: {title}", action, "", "Duplicate links:"]
    for i, (u, why) in enumerate(r.removed + r.extra_dup, 1):
        report.append(f"{i}. {u}\n   ↳ {why}")
    report += ["", f"📊 Total duplicate: {r.total_dup}"]
    notify_owner("\n".join(report))


@bot.channel_post_handler(content_types=CHANNEL_TYPES)
def process_channel_post(message):
    POST_POOL.submit(_run_safe, handle_channel_post, message, False)


@bot.edited_channel_post_handler(content_types=CHANNEL_TYPES)
def process_edited_channel_post(message):
    POST_POOL.submit(_run_safe, handle_channel_post, message, True)


# ============================================================
# PRIVATE / GROUP POSTS (main flow)
# ============================================================
def _process_post(message):
    if message.text and message.text.startswith('/'):
        return
    text = message.text or message.caption or ""

    if contains_blocked_text(text):
        _stat("non_text_deleted")
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        return

    owner = f"{message.chat.id}:{message.message_id}"
    r = analyze(message, owner)
    removed = r.removed

    # Sab links duplicate -> poori post skip + 🚫 alert
    if r.all_dup:
        _stat("dup_blocked")
        reasons = list(dict.fromkeys(why for _, why in r.removed + r.extra_dup))
        if alert_on("alert_duplicate"):
            alert_button(message, "🚫 DELETE DUPLICATE LINK 🚫",
                         f"({r.total_dup} link duplicate the)\n" + "\n".join(f"• {w}" for w in reasons))
        return

    # Surrounding text OFF: sirf links rakho; ek bhi link na bache to post skip
    surround_off = not surrounding_text_enabled()
    off_links = None
    if surround_off:
        off_links = build_links_only(message, r)
        if not off_links:
            send_now(message.chat.id, lambda: bot.reply_to(
                message, "⚠️ Post me koi link nahi mila (Surrounding text OFF hai)."))
            return

    code = LINKDB.new_code() if bot_data.get("unique_code_status", True) else None
    processed = r.cleaned if removed else r.text
    if surround_off:
        processed = "\n\n".join(off_links)

    # Telegram link -> aapke channel link se replace
    link_replaced = False
    my_link = bot_data.get("my_channel_link") or ""
    if my_link and bot_data.get("link_replace_status", True):
        for url in URL_RE.findall(processed):
            clean = url.rstrip(TRAIL)
            if ("t.me" in clean.lower() or "telegram.me" in clean.lower()) and my_link not in clean:
                processed = processed.replace(clean, my_link)
                link_replaced = True

    if surround_off:                       # replace ke baad same link dobara aaye to ek hi rakho
        processed = "\n\n".join(dict.fromkeys(ln for ln in processed.split("\n") if ln.strip()))

    if removed:
        _stat("links_removed", len(removed))

    # 🧹 clear alert
    if removed and alert_on("alert_clear"):
        alert_button(message, f"🧹 CLEAR OF LINKS {len(removed)} 🧹",
                     f"{len(removed)} duplicate link hata di gayi.\n{r.total_new} new link aage bhej di gayi.")
    # ♻️ replace alert
    if link_replaced and alert_on("alert_replace"):
        alert_button(message, "♻️ REPLACE WITH YOUR CHANNEL LINK ♻️",
                     "Telegram link aapke channel link se replace kar diya gaya.")

    prefix = f"📌 Post Code: {code}\n\n" if code else ""
    limit = 4096 if message.content_type == 'text' else 1024
    final_text, fmt = build_final(processed, prefix, limit)

    spec = None
    if bot_data.get("button_text") and bot_data.get("button_url"):
        spec = [bot_data["button_text"], bot_data["button_url"]]

    # Reply + har channel ka alag job: pehle disk par save, phir bhejna (crash par bhi kuch nahi khota)
    jobs = [make_job(owner, message, message.chat.id, final_text, spec, True, fmt)]
    for ch in list(bot_data["channels"]):
        jobs.append(make_job(owner, message, ch, final_text, spec, False, fmt))
    journal_commit(owner, [j for j in jobs if j["msg"] is not None])
    for j in jobs:
        dispatch_job(j, message, fast=j["reply"])


def _run_post(message, attempt=0, restored=False):
    """Error aaye to post band nahi hoti: 3-60 sec baad dobara try, MAX_POST_ATTEMPTS tak."""
    if message.text and message.text.startswith('/'):
        return
    key = f"{message.chat.id}:{message.message_id}"
    if attempt == 0:
        _stat("restored" if restored else "received")
    t0 = time.time()
    try:
        _process_post(message)
    except Exception as e:
        attempt += 1
        print(f"Handler error (try {attempt}/{MAX_POST_ATTEMPTS}): {e}")
        traceback.print_exc()
        if attempt >= MAX_POST_ATTEMPTS:
            _stat("failed")
            journal_remove_post(key)
            notify_owner(f"⚠️ Ek post {MAX_POST_ATTEMPTS} koshish ke baad bhi process nahi ho payi: {key}\nError: {e}")
            return
        _later(min(60, 3 * attempt), lambda: POST_POOL.submit(_run_post, message, attempt))
        return
    _timing("proc", time.time() - t0)
    journal_remove_post(key)


@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'animation', 'audio'])
def process_post(message):
    if message.text and message.text.startswith('/'):
        return
    if message.chat.type == "private" and not is_admin(message.from_user.id if message.from_user else None):
        return                          # private me sirf owner/admin ki post process hoti hai
    key = f"{message.chat.id}:{message.message_id}"
    with JOURNAL_LOCK:
        if key in INFLIGHT:
            return
        INFLIGHT.add(key)
    journal_add_post(key, message)
    POST_POOL.submit(_run_post, message)


def recover_pending():
    """Start par: adhoore posts/jobs dobara chalu karo."""
    with JOURNAL_LOCK:
        posts = list(JOURNAL["posts"].items())
        jobs = list(JOURNAL["jobs"].values())
    n = 0
    for key, d in posts:
        try:
            msg = telebot.types.Message.de_json(d)
        except Exception as e:
            print(f"Restore error (post {key}): {e}")
            journal_remove_post(key)
            continue
        POST_POOL.submit(_run_post, msg, 0, True)
        n += 1
    for job in jobs:
        try:
            msg = telebot.types.Message.de_json(job.get("msg"))
        except Exception as e:
            print(f"Restore error (job {job.get('id')}): {e}")
            journal_remove_job(job["id"])
            continue
        dispatch_job(job, msg, fast=False)
        n += 1
    if n:
        print(f"{n} adhoore kaam dobara chalu kiye ({JOURNAL_FILE} se).")


# ============================================================
# MAIN (crash-proof infinity polling)
# ============================================================
def _excepthook(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)
    try:
        notify_owner(f"🔥 Bot me unexpected error: {exc_type.__name__}: {exc}")
    except Exception:
        pass


sys.excepthook = _excepthook

if __name__ == "__main__":
    if not TOKEN or TOKEN == "PASTE_NEW_TOKEN_HERE":
        print("Pehle .env file me BOT_TOKEN=... likho (BotFather se), ya file me TOKEN set karo.")
        raise SystemExit(1)
    # Ek hi instance: dobara chalne par 409 Conflict aata hai, isliye lock lete hain
    _lock_fh = open(LOCK_FILE, "w")
    if fcntl is not None:
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Bot pehle se chal raha hai (lock mila nahi). Ye instance band ho raha hai.")
            raise SystemExit(0)
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()

    MERGE_REPORT = []
    try:
        MERGE_REPORT = merge_all_sources()
    except Exception as e:
        print(f"Auto-merge error: {e}")
        traceback.print_exc()
    recover_pending()

    try:
        bot.set_my_commands([
            BotCommand("start", "Bot shuru karein"),
            BotCommand("help", "Sabhi commands dekhein"),
            BotCommand("add_header", "Header text add karein"),
            BotCommand("remove_header", "Header hatayein"),
            BotCommand("add_footer", "Footer text add karein"),
            BotCommand("remove_footer", "Footer hatayein"),
            BotCommand("set_button", "Inline button lagayein"),
            BotCommand("remove_button", "Inline button hatayein"),
            BotCommand("format_on", "Header/Footer formatting ON"),
            BotCommand("format_off", "Header/Footer formatting OFF"),
            BotCommand("format_list", "Formatting ki list (number ke saath)"),
            BotCommand("remove_format", "Format hatao: /remove_format [no.]"),
            BotCommand("uniq_code_on", "Unique Post Code ON"),
            BotCommand("uniq_code_off", "Unique Post Code OFF"),
            BotCommand("set_mylink", "Aapke channel ka replacement link"),
            BotCommand("link_replace_on", "Telegram link replace ON"),
            BotCommand("link_replace_off", "Telegram link replace OFF"),
            BotCommand("enable_text", "Surrounding text ON (links ke saath text bhi)"),
            BotCommand("disable_text", "Surrounding text OFF (sirf links jayenge)"),
            BotCommand("replace_notification_on", "Replace link alert ON"),
            BotCommand("replace_notification_off", "Replace link alert OFF"),
            BotCommand("duplicate_notification_on", "Duplicate links alert ON"),
            BotCommand("duplicate_notification_off", "Duplicate links alert OFF"),
            BotCommand("clear_notification_on", "Clear of links alert ON"),
            BotCommand("clear_notification_off", "Clear of links alert OFF"),
            BotCommand("add_channel", "Auto-forward channel jodein"),
            BotCommand("channel_list", "Channels ki list"),
            BotCommand("remove_channel", "Channel hatayein"),
            BotCommand("non_text", "Aisa text jo aate hi delete ho"),
            BotCommand("non_text_list", "Block text ki list"),
            BotCommand("remove_non_text", "Block text hatayein"),
            BotCommand("regex_add", "Regex filter jodein"),
            BotCommand("wild_add", "Wildcard filter jodein"),
            BotCommand("regex_list", "Filters ki list"),
            BotCommand("regex_remove", "Filter hatayein"),
            BotCommand("add_admin", "Admin jodein (sirf owner)"),
            BotCommand("remove_admin", "Admin hatayein (sirf owner)"),
            BotCommand("admin_list", "Admins ki list (sirf owner)"),
            BotCommand("merge_now", "Saari json/db files merge_links.db me merge"),
            BotCommand("download_links", "Saare saved links .txt file me download"),
            BotCommand("clear_links", "Saved links saaf (sirf owner)"),
            BotCommand("status", "Live status (status reset)"),
        ])
        pass
    except Exception as e:
        print(f"Commands set karne me error: {e}")

    try:
        bot.send_message(OWNER_ID, f"✅ {BOT_NAME} (Max Threads) chalu ho gaya.\n"
                                   f"Deep check: {'ON' if DEEP_CHECK else 'OFF'}\n\n"
                                   "🗃 MERGE REPORT\n" + ("\n".join(MERGE_REPORT)[:3500] if MERGE_REPORT
                                                          else f"📚 {os.path.basename(DB_FILE)}: {LINKDB.count_links()} links"))
    except Exception as e:
        print(f"Start message owner ko nahi gaya (owner ne bot ko /start kiya?): {e}")

    if LINKDB.count_links() == 0:
        print("WARNING: merge_links.db khali hai. Purani .json/.db files bot ke folder me rakho (ya merge_in folder me), phir /merge_now chalao.")
    try:
        print("BOT CHALU HO GYA MAMU .......😁")
    except UnicodeEncodeError:          # purane console (Windows) par emoji na chhape to bot crash na ho
        print("BOT CHALU HO GYA MAMU .......😁".encode("utf-8", "replace").decode("ascii", "replace"))
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=False,
                                 allowed_updates=["message", "channel_post",
                                                  "edited_channel_post", "callback_query"])
        except Exception as e:
            hint = " (409: kahin aur bhi bot chal raha hai, ek hi rakho)" if "409" in str(e) else ""
            print(f"Polling crashed: {e}{hint}. 10 sec me restart ho raha hai...")
            time.sleep(10)
