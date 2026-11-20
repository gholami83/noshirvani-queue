import json
import sqlite3
import socket
import os
import sys
import tempfile
from collections import OrderedDict

import eventlet
eventlet.monkey_patch()

import eventlet.tpool
import eventlet.patcher
real_subprocess = eventlet.patcher.original('subprocess')

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'noshirvani-babol-secret-2025'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet',
                    ping_interval=25, ping_timeout=60)

with open('config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

DB_PATH = 'queue.db'


# ═══════════════════════════════════════════════════════════
#              ⚙️ تنظیمات صدا — اینجا عوض کن
# ═══════════════════════════════════════════════════════════
# صدای مرد: "fa-IR-FaridNeural"  |  صدای زن: "fa-IR-DilaraNeural"
TTS_VOICE = "fa-IR-FaridNeural"

# سرعت: None (پیش‌فرض) | "-10%" (کندتر) | "+10%" (تندتر)
TTS_RATE = None

# زیر و بمی: None | "-5Hz" (بم‌تر) | "+5Hz" (زیرتر)
TTS_PITCH = None

# حجم: None | "-10%" | "+10%"
TTS_VOLUME = None

# حداکثر تعداد فایل صوتی در حافظه
TTS_CACHE_MAX = 500


# ═══════════════════════════════════════════════════════════
#              TTS Cache
# ═══════════════════════════════════════════════════════════
tts_cache = OrderedDict()


# ═══════════════════════════════════════════════════════════
#              توابع کمکی
# ═══════════════════════════════════════════════════════════
def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_persian_digits(s):
    m = {'0':'۰','1':'۱','2':'۲','3':'۳','4':'۴','5':'۵','6':'۶','7':'۷','8':'۸','9':'۹'}
    return ''.join(m.get(c, c) for c in str(s))


def make_tts_key(number, counter_id):
    return (str(int(str(number))), str(int(str(counter_id))))


def make_tts_text(number, counter_id):
    num_clean = str(int(str(number)))
    cid_clean = str(int(str(counter_id)))
    return f"نوبت {to_persian_digits(num_clean)}، باجه شماره {to_persian_digits(cid_clean)}"


def _synthesize_speech_sync(text):
    """تولید صدا در thread واقعی (tpool) — hub رو بلاک نمی‌کنه"""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        try:
            # ساخت دستور به صورت شرطی
            cmd = [sys.executable, '-m', 'edge_tts', '--voice', TTS_VOICE]
            if TTS_RATE is not None:
                cmd.extend(['--rate', TTS_RATE])
            if TTS_PITCH is not None:
                cmd.extend(['--pitch', TTS_PITCH])
            if TTS_VOLUME is not None:
                cmd.extend(['--volume', TTS_VOLUME])
            cmd.extend(['--text', text, '--write-media', tmp_path])

            proc = real_subprocess.Popen(
                cmd,
                stdout=real_subprocess.PIPE,
                stderr=real_subprocess.PIPE,
            )
            stdout, stderr = proc.communicate(timeout=15)

            if proc.returncode != 0:
                print(f'[edge-tts ERROR] returncode={proc.returncode}')
                print(f'[edge-tts ERROR] stderr: {stderr.decode(errors="ignore")[:800]}')
                return None

            with open(tmp_path, 'rb') as f:
                audio = f.read()

            if len(audio) < 100:
                print(f'[edge-tts] Audio too small: {len(audio)} bytes')
                return None

            return audio
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f'[edge-tts FATAL] {type(e).__name__}: {e}')
        return None


def synthesize_speech(text):
    """TTS با tpool — non-blocking نسبت به eventlet hub"""
    try:
        return eventlet.tpool.execute(_synthesize_speech_sync, text)
    except Exception as e:
        print(f'[tpool TTS ERROR] {e}')
        return None


def pregenerate_tts(number, counter_id):
    try:
        key = make_tts_key(number, counter_id)
        if key in tts_cache:
            return
        text = make_tts_text(number, counter_id)
        audio = synthesize_speech(text)
        if audio:
            if len(tts_cache) >= TTS_CACHE_MAX:
                tts_cache.popitem(last=False)
            tts_cache[key] = audio
            print(f'[TTS CACHE] {len(audio)} bytes for {key} — "{text}"')
    except Exception as e:
        print(f'[TTS CACHE ERROR] {e}')


@app.route('/tts/<number>/<counter_id>')
def get_tts(number, counter_id):
    try:
        key = make_tts_key(number, counter_id)
        if key in tts_cache:
            return Response(tts_cache[key], mimetype='audio/mpeg',
                            headers={'Cache-Control': 'public, max-age=3600'})
        text = make_tts_text(number, counter_id)
        audio = synthesize_speech(text)
        if audio:
            if len(tts_cache) >= TTS_CACHE_MAX:
                tts_cache.popitem(last=False)
            tts_cache[key] = audio
            return Response(audio, mimetype='audio/mpeg',
                            headers={'Cache-Control': 'public, max-age=3600'})
    except Exception as e:
        print(f'[TTS ERROR] {e}')
    return "TTS Error", 500


# ═══════════════════════════════════════════════════════════
#              دیتابیس + Migration
# ═══════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL,
        counter_id INTEGER DEFAULT 0,
        status TEXT NOT NULL,
        ticket_date TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        called_at TEXT,
        finished_at TEXT
    )''')

    c.execute("PRAGMA table_info(tickets)")
    cols = [row[1] for row in c.fetchall()]
    if 'ticket_date' not in cols:
        print('[MIGRATION] Adding ticket_date column')
        c.execute("ALTER TABLE tickets ADD COLUMN ticket_date TEXT")
        c.execute("UPDATE tickets SET ticket_date = DATE(issued_at) WHERE ticket_date IS NULL")

    c.execute('''CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        name TEXT NOT NULL,
        counter_id INTEGER,
        created_at TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS counters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )''')
    conn.commit()

    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        print('[MIGRATION] Seed users')
        now = now_str()
        for u, v in CONFIG['users'].items():
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                      (u, v['pass'], v['role'], v['name'], v.get('counter_id'), now))

    c.execute('SELECT COUNT(*) FROM counters')
    if c.fetchone()[0] == 0:
        print('[MIGRATION] Seed counters')
        now = now_str()
        for cfg_c in CONFIG['counters']:
            c.execute('INSERT INTO counters (id, name, is_active, created_at) VALUES (?,?,1,?)',
                      (cfg_c['id'], cfg_c['name'], now))

    conn.commit()
    conn.close()

    # فیکس ۵: پاکسازی نوبت‌های نصفه‌شب
    cleanup_old_serving()


def _conn():
    return sqlite3.connect(DB_PATH)


def get_meta(key, default="0"):
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT value FROM meta WHERE key=?', (key,))
    row = c.fetchone(); conn.close()
    return row[0] if row else default


def set_meta(key, value):
    conn = _conn(); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit(); conn.close()


# فیکس ۵
def cleanup_old_serving():
    conn = _conn(); c = conn.cursor()
    c.execute('''UPDATE tickets SET status="done", finished_at=?
                 WHERE status="serving" AND ticket_date < ?''',
              (now_str(), today_str()))
    affected = c.rowcount
    conn.commit(); conn.close()
    if affected > 0:
        print(f'[CLEANUP] {affected} old serving tickets marked as done')


# ---------- users ----------
def db_get_user(username):
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT username, password, role, name, counter_id FROM users WHERE username=?', (username,))
    r = c.fetchone(); conn.close()
    if not r: return None
    return {"username": r[0], "pass": r[1], "role": r[2], "name": r[3], "counter_id": r[4]}


def db_all_users():
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT username, role, name, counter_id FROM users ORDER BY role, username')
    rows = c.fetchall(); conn.close()
    return [{"username": r[0], "role": r[1], "name": r[2], "counter_id": r[3]} for r in rows]


def db_add_user(username, password, role, name, counter_id=None):
    # فیکس ۶: چک counter_id برای باجه
    if role == 'counter' and not counter_id:
        return False, "برای نقش باجه، انتخاب یک باجه الزامی است"
    conn = _conn(); c = conn.cursor()
    try:
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                  (username, password, role, name, counter_id, now_str()))
        conn.commit()
        return True, "کاربر اضافه شد"
    except sqlite3.IntegrityError:
        return False, "این نام کاربری قبلاً وجود دارد"
    finally:
        conn.close()


def db_delete_user(username):
    conn = _conn(); c = conn.cursor()
    c.execute('DELETE FROM users WHERE username=? AND role != "admin"', (username,))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


# ---------- counters ----------
def db_all_counters(active_only=False):
    conn = _conn(); c = conn.cursor()
    q = 'SELECT id, name, is_active FROM counters'
    if active_only: q += ' WHERE is_active=1'
    q += ' ORDER BY id'
    c.execute(q)
    rows = c.fetchall(); conn.close()
    return [{"id": r[0], "name": r[1], "is_active": bool(r[2])} for r in rows]


def db_add_counter(name):
    conn = _conn(); c = conn.cursor()
    c.execute('INSERT INTO counters (name, is_active, created_at) VALUES (?, 1, ?)',
              (name, now_str()))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def db_delete_counter(cid):
    conn = _conn(); c = conn.cursor()
    # فیکس ۴: نوبت‌های serving این باجه رو done کن
    c.execute('''UPDATE tickets SET status="done", finished_at=?
                 WHERE status="serving" AND counter_id=?''', (now_str(), cid))
    c.execute('UPDATE counters SET is_active=0 WHERE id=?', (cid,))
    c.execute('UPDATE users SET counter_id=NULL WHERE counter_id=?', (cid,))
    conn.commit(); conn.close()


# ---------- tickets ----------
def next_ticket_number(ticket_date=None):
    if not ticket_date:
        ticket_date = today_str()
    key_last = f'last_number_{ticket_date}'
    last = int(get_meta(key_last, '0'))
    new_num = last + 1
    set_meta(key_last, new_num)
    return str(new_num).zfill(3)


def db_add_ticket(number, ticket_date):
    conn = _conn(); c = conn.cursor()
    c.execute('''INSERT INTO tickets (number, counter_id, status, ticket_date, issued_at) 
                 VALUES (?, 0, ?, ?, ?)''',
              (number, 'waiting', ticket_date, now_str()))
    conn.commit(); conn.close()


def db_get_serving(counter_id, target_date=None):
    if not target_date:
        target_date = today_str()
    conn = _conn(); c = conn.cursor()
    c.execute('''SELECT id, number FROM tickets 
                 WHERE status="serving" AND counter_id=? AND ticket_date=?''',
              (counter_id, target_date))
    r = c.fetchone(); conn.close()
    return r


def db_call_next(counter_id, target_date=None):
    if not target_date:
        target_date = today_str()
    if db_get_serving(counter_id, target_date):
        return ("BUSY", None)
    conn = _conn(); c = conn.cursor()
    c.execute('''SELECT id, number FROM tickets 
                 WHERE status="waiting" AND ticket_date=?
                 ORDER BY id ASC LIMIT 1''', (target_date,))
    row = c.fetchone()
    if row:
        tid, number = row
        # فیکس ۱: UPDATE اتمیک
        c.execute('''UPDATE tickets SET status="serving", counter_id=?, called_at=? 
                     WHERE id=? AND status="waiting"''',
                  (counter_id, now_str(), tid))
        if c.rowcount == 0:
            conn.commit(); conn.close()
            return ("EMPTY", None)
        conn.commit(); conn.close()
        return ("OK", number)
    conn.commit(); conn.close()
    return ("EMPTY", None)


def db_finish(counter_id, target_date=None):
    if not target_date:
        target_date = today_str()
    conn = _conn(); c = conn.cursor()
    c.execute('''UPDATE tickets SET status="done", finished_at=? 
                 WHERE status="serving" AND counter_id=? AND ticket_date=?''',
              (now_str(), counter_id, target_date))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def db_stats(target_date=None):
    if not target_date:
        target_date = today_str()
    conn = _conn(); c = conn.cursor()

    c.execute('SELECT COUNT(*) FROM tickets WHERE status="waiting" AND ticket_date=?', (target_date,))
    waiting = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM tickets WHERE status="serving" AND ticket_date=?', (target_date,))
    serving = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM tickets WHERE status="done" AND ticket_date=?', (target_date,))
    done = c.fetchone()[0]

    counters_info = []
    for cfg_c in db_all_counters(active_only=True):
        cid = cfg_c['id']
        c.execute('''SELECT number FROM tickets 
                     WHERE status="serving" AND counter_id=? AND ticket_date=?''',
                  (cid, target_date))
        r = c.fetchone()
        counters_info.append({
            "id": cid,
            "name": cfg_c['name'],
            "current_ticket": r[0] if r else None,
        })
    conn.close()
    return waiting, serving, done, counters_info


def db_recent_waiting(limit=10, target_date=None):
    if not target_date:
        target_date = today_str()
    conn = _conn(); c = conn.cursor()
    c.execute('''SELECT number, issued_at FROM tickets 
                 WHERE status="waiting" AND ticket_date=?
                 ORDER BY id ASC LIMIT ?''', (target_date, limit))
    rows = c.fetchall(); conn.close()
    return [{"number": r[0], "issued_at": r[1]} for r in rows]


def db_reset_queue(reset_numbers=False, target_date=None):
    if not target_date:
        target_date = today_str()
    conn = _conn(); c = conn.cursor()
    c.execute('DELETE FROM tickets WHERE ticket_date=?', (target_date,))
    conn.commit(); conn.close()
    if reset_numbers:
        set_meta(f'last_number_{target_date}', 0)


def db_available_dates():
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT DISTINCT ticket_date FROM tickets ORDER BY ticket_date DESC LIMIT 30')
    rows = c.fetchall(); conn.close()
    return [r[0] for r in rows]


# ═══════════════════════════════════════════════════════════
#              وضعیت آنلاین
# ═══════════════════════════════════════════════════════════
online_sessions = {}


def count_online(role):
    return sum(1 for s in online_sessions.values() if s['role'] == role)


def online_counter_ids():
    return {s['counter_id'] for s in online_sessions.values()
            if s['role'] == 'counter' and s['counter_id'] is not None}


def build_stats(target_date=None):
    if not target_date:
        target_date = today_str()
    waiting, serving, done, counters_info = db_stats(target_date)
    online_cids = online_counter_ids()
    for c in counters_info:
        c['is_online'] = c['id'] in online_cids
    return {
        "waiting_count": waiting, "serving_count": serving, "served_total": done,
        "counters": counters_info,
        "online_receptions": count_online('reception'),
        "online_announcers": count_online('announcer'),
        "online_counters": count_online('counter'),
        "waiting_list": db_recent_waiting(target_date=target_date),
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%Y/%m/%d"),
        "today": today_str(),
        "target_date": target_date,
        "available_dates": db_available_dates(),
    }


def broadcast_stats(target_date=None):
    socketio.emit('stats_update', build_stats(target_date), room='global')


# ═══════════════════════════════════════════════════════════
#                    روت‌ها
# ═══════════════════════════════════════════════════════════
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = db_get_user(username)
        if user and user['pass'] == password:
            session['user'] = username
            session['role'] = user['role']
            session['name'] = user['name']
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            if user['role'] == 'reception':
                return redirect(url_for('reception_dashboard'))
            if user['role'] == 'counter':
                session['counter_id'] = user['counter_id']
                return redirect(url_for('counter_dashboard'))
            if user['role'] == 'announcer':
                return redirect(url_for('announcer_dashboard'))
        return render_template('index.html', error="نام کاربری یا رمز اشتباه است", uni=CONFIG['university'])
    return render_template('index.html', uni=CONFIG['university'])


@app.route('/reception')
def reception_dashboard():
    if session.get('role') != 'reception':
        return redirect(url_for('login'))
    return render_template('reception.html', name=session['name'],
                           counters=db_all_counters(active_only=True),
                           today=today_str(),
                           available_dates=db_available_dates(),
                           uni=CONFIG['university'])


@app.route('/counter')
def counter_dashboard():
    if session.get('role') != 'counter':
        return redirect(url_for('login'))
    cid = session['counter_id']
    cinfo = next((c for c in db_all_counters() if c['id'] == cid), None)
    if not cinfo or not cinfo['is_active']:
        session.clear()
        return redirect(url_for('login'))
    return render_template('counter.html', name=session['name'], counter_id=cid,
                           counter_name=cinfo['name'],
                           today=today_str(),
                           available_dates=db_available_dates(),
                           uni=CONFIG['university'])


@app.route('/announcer')
def announcer_dashboard():
    if session.get('role') != 'announcer':
        return redirect(url_for('login'))
    return render_template('announcer.html', name=session['name'],
                           counters=db_all_counters(active_only=True),
                           today=today_str(),
                           available_dates=db_available_dates(),
                           uni=CONFIG['university'])


@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('admin.html',
                           name=session['name'],
                           uni=CONFIG['university'],
                           users=db_all_users(),
                           counters=db_all_counters(),
                           stats=build_stats(),
                           today=today_str(),
                           available_dates=db_available_dates())


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/stats')
def api_stats():
    td = request.args.get('date', today_str())
    return jsonify(build_stats(td))


@app.route('/api/history/today')
def api_history_today():
    td = request.args.get('date', today_str())
    conn = _conn(); c = conn.cursor()
    c.execute('''SELECT number, counter_id, status, issued_at, called_at, finished_at
                 FROM tickets WHERE ticket_date=?
                 ORDER BY id DESC LIMIT 100''', (td,))
    rows = c.fetchall(); conn.close()
    return jsonify([{
        "number": r[0], "counter_id": r[1], "status": r[2],
        "issued_at": r[3], "called_at": r[4], "finished_at": r[5],
    } for r in rows])


@app.route('/api/available-dates')
def api_available_dates():
    return jsonify(db_available_dates())


# ═══════════════════════════════════════════════════════════
#              Socket.IO
# ═══════════════════════════════════════════════════════════
@socketio.on('connect')
def on_connect():
    join_room('global')
    print(f'[CONNECT] {request.sid}')
    emit('stats_update', build_stats())


@socketio.on('register_role')
def on_register(data):
    role = data.get('role')
    cid = data.get('counter_id')
    if role in ('reception', 'counter', 'announcer', 'admin'):
        online_sessions[request.sid] = {
            "role": role,
            "counter_id": int(cid) if cid else None
        }
        print(f'[REGISTER] {request.sid} → {role} (counter={cid})')
        broadcast_stats()


@socketio.on('disconnect')
def on_disconnect():
    if request.sid in online_sessions:
        info = online_sessions.pop(request.sid)
        print(f'[DISCONNECT] {request.sid} was {info["role"]}')
    broadcast_stats()


@socketio.on('issue_ticket')
def on_issue_ticket(data=None):
    ticket_date = (data or {}).get('ticket_date') or today_str()
    try:
        datetime.strptime(ticket_date, "%Y-%m-%d")
    except ValueError:
        emit('error_msg', {'msg': 'تاریخ نامعتبر است'}); return

    number = next_ticket_number(ticket_date)
    db_add_ticket(number, ticket_date)
    broadcast_stats(ticket_date)
    socketio.emit('ticket_issued', {
        "number": number,
        "ticket_date": ticket_date,
    }, room='global')
    emit('issue_ok', {'msg': f'🎫 نوبت {number} برای تاریخ {ticket_date} صادر شد'})


@socketio.on('call_next')
def on_call_next(data):
    counter_id = int(data['counter_id'])
    target_date = data.get('target_date') or today_str()
    status, number = db_call_next(counter_id, target_date)
    if status == "OK":
        # اول emit کن (سریع)
        payload = {
            "counter_id": counter_id,
            "ticket": {"number": number},
            "target_date": target_date,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        socketio.emit('ticket_called', payload, room='global')
        broadcast_stats(target_date)
        # بعد TTS رو در پس‌زمینه شروع کن
        eventlet.spawn_n(pregenerate_tts, number, counter_id)
    elif status == "BUSY":
        current = db_get_serving(counter_id, target_date)
        emit('error_msg', {'msg': f'⚠️ هنوز نوبت {current[1]} رو تموم نکردی'})
        broadcast_stats(target_date)
    elif status == "EMPTY":
        emit('error_msg', {'msg': f'صف {target_date} خالیه'})
        broadcast_stats(target_date)


@socketio.on('finish_current')
def on_finish(data):
    counter_id = int(data['counter_id'])
    target_date = data.get('target_date') or today_str()
    ok = db_finish(counter_id, target_date)
    if ok:
        emit('finish_ok', {'msg': '✅ نوبت تموم شد'})
    else:
        emit('error_msg', {'msg': 'شما نوبت فعالی ندارید'})
    broadcast_stats(target_date)


# ---------- ادمین ----------
def _is_admin():
    return online_sessions.get(request.sid, {}).get('role') == 'admin'


@socketio.on('admin_add_counter')
def admin_add_counter(data):
    if not _is_admin():
        emit('error_msg', {'msg': 'دسترسی ندارید'}); return
    name = data.get('name', '').strip()
    if not name:
        emit('error_msg', {'msg': 'نام باجه خالی است'}); return
    new_id = db_add_counter(name)
    broadcast_stats()
    emit('admin_ok', {'msg': f'✅ باجه با شناسه {new_id} اضافه شد'})
    socketio.emit('admin_refresh', {}, room='global')


@socketio.on('admin_delete_counter')
def admin_delete_counter(data):
    if not _is_admin():
        emit('error_msg', {'msg': 'دسترسی ندارید'}); return
    cid = int(data.get('counter_id', 0))
    db_delete_counter(cid)
    broadcast_stats()
    emit('admin_ok', {'msg': f'🗑️ باجه {cid} غیرفعال شد'})
    socketio.emit('admin_refresh', {}, room='global')


@socketio.on('admin_add_user')
def admin_add_user(data):
    if not _is_admin():
        emit('error_msg', {'msg': 'دسترسی ندارید'}); return
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role')
    name = data.get('name', '').strip()
    cid = data.get('counter_id')

    if not all([username, password, role, name]):
        emit('error_msg', {'msg': 'همه فیلدها الزامی است'}); return
    if role not in ('reception', 'counter', 'announcer', 'admin'):
        emit('error_msg', {'msg': 'نقش نامعتبر'}); return

    counter_id = int(cid) if cid and role == 'counter' else None
    ok, msg = db_add_user(username, password, role, name, counter_id)
    if ok:
        emit('admin_ok', {'msg': '✅ ' + msg})
        socketio.emit('admin_refresh', {}, room='global')
    else:
        emit('error_msg', {'msg': msg})


@socketio.on('admin_delete_user')
def admin_delete_user(data):
    if not _is_admin():
        emit('error_msg', {'msg': 'دسترسی ندارید'}); return
    username = data.get('username', '')
    if username == 'admin':
        emit('error_msg', {'msg': 'نمی‌توان ادمین اصلی را حذف کرد'}); return
    ok = db_delete_user(username)
    if ok:
        emit('admin_ok', {'msg': f'🗑️ کاربر {username} حذف شد'})
        socketio.emit('admin_refresh', {}, room='global')
    else:
        emit('error_msg', {'msg': 'کاربر یافت نشد'})


@socketio.on('admin_reset_queue')
def admin_reset_queue(data=None):
    if not _is_admin():
        emit('error_msg', {'msg': 'دسترسی ندارید'}); return
    reset_numbers = bool(data and data.get('reset_numbers'))
    target_date = (data or {}).get('target_date') or today_str()
    db_reset_queue(reset_numbers=reset_numbers, target_date=target_date)
    broadcast_stats(target_date)
    msg = f'🔄 صف {target_date} ریست شد' + (' (شماره هم از 001)' if reset_numbers else '')
    emit('admin_ok', {'msg': msg})
    socketio.emit('admin_refresh', {}, room='global')


# ═══════════════════════════════════════════════════════════
#                    اجرا
# ═══════════════════════════════════════════════════════════
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


init_db()

if __name__ == '__main__':
    ip = get_local_ip()
    print("\n" + "="*60)
    print(f"  🖥️  دسترسی از خود لپ‌تاپ:      http://127.0.0.1:5000")
    print(f"  📱  دسترسی از شبکه (بقیه):   http://{ip}:5000")
    print(f"  📅  تاریخ امروز:              {today_str()}")
    print(f"  🔊 صدای TTS:                  {TTS_VOICE}")
    print(f"  ⚙️  Rate: {TTS_RATE} | Pitch: {TTS_PITCH} | Volume: {TTS_VOLUME}")
    print("="*60 + "\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False)