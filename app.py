import json
import sqlite3
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'noshirvani-babol-secret-2025'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

with open('config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

DB_PATH = 'queue.db'


# ═══════════════════════════════════════════════════════════
#                    دیتابیس + Migration
# ═══════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL,
        counter_id INTEGER DEFAULT 0,
        status TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        called_at TEXT,
        finished_at TEXT
    )''')
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

    # Migration از config.json (اولین اجرا)
    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        print('[MIGRATION] Seed users')
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for u, v in CONFIG['users'].items():
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                      (u, v['pass'], v['role'], v['name'], v.get('counter_id'), now))

    c.execute('SELECT COUNT(*) FROM counters')
    if c.fetchone()[0] == 0:
        print('[MIGRATION] Seed counters')
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for cfg_c in CONFIG['counters']:
            c.execute('INSERT INTO counters (id, name, is_active, created_at) VALUES (?,?,1,?)',
                      (cfg_c['id'], cfg_c['name'], now))

    conn.commit()
    conn.close()


def _conn():
    return sqlite3.connect(DB_PATH)


# ---------- meta ----------
def get_meta(key, default="0"):
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT value FROM meta WHERE key=?', (key,))
    row = c.fetchone(); conn.close()
    return row[0] if row else default


def set_meta(key, value):
    conn = _conn(); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit(); conn.close()


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
    conn = _conn(); c = conn.cursor()
    try:
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                  (username, password, role, name, counter_id,
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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
              (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id


def db_delete_counter(cid):
    conn = _conn(); c = conn.cursor()
    c.execute('UPDATE counters SET is_active=0 WHERE id=?', (cid,))
    c.execute('UPDATE users SET counter_id=NULL WHERE counter_id=?', (cid,))
    conn.commit(); conn.close()


# ---------- tickets ----------
def next_ticket_number():
    last = int(get_meta('last_number', '0'))
    new_num = last + 1
    set_meta('last_number', new_num)
    return str(new_num).zfill(3)


def db_add_ticket(number):
    conn = _conn(); c = conn.cursor()
    c.execute('INSERT INTO tickets (number, counter_id, status, issued_at) VALUES (?,0,?,?)',
              (number, 'waiting', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()


def db_get_serving(counter_id):
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT id, number FROM tickets WHERE status="serving" AND counter_id=?', (counter_id,))
    r = c.fetchone(); conn.close()
    return r


def db_call_next(counter_id):
    if db_get_serving(counter_id):
        return ("BUSY", None)
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT id, number FROM tickets WHERE status="waiting" ORDER BY id ASC LIMIT 1')
    row = c.fetchone()
    if row:
        tid, number = row
        c.execute('UPDATE tickets SET status="serving", counter_id=?, called_at=? WHERE id=?',
                  (counter_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tid))
        conn.commit(); conn.close()
        return ("OK", number)
    conn.commit(); conn.close()
    return ("EMPTY", None)


def db_finish(counter_id):
    conn = _conn(); c = conn.cursor()
    c.execute('UPDATE tickets SET status="done", finished_at=? WHERE status="serving" AND counter_id=?',
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), counter_id))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    return ok


def db_stats():
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM tickets WHERE status="waiting"')
    waiting = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM tickets WHERE status="serving"')
    serving = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM tickets WHERE status="done"')
    done = c.fetchone()[0]

    counters_info = []
    for cfg_c in db_all_counters(active_only=True):
        cid = cfg_c['id']
        c.execute('SELECT number FROM tickets WHERE status="serving" AND counter_id=?', (cid,))
        r = c.fetchone()
        counters_info.append({"id": cid, "name": cfg_c['name'],
                              "current_ticket": r[0] if r else None})
    conn.close()
    return waiting, serving, done, counters_info


def db_recent_waiting(limit=10):
    conn = _conn(); c = conn.cursor()
    c.execute('SELECT number, issued_at FROM tickets WHERE status="waiting" ORDER BY id ASC LIMIT ?', (limit,))
    rows = c.fetchall(); conn.close()
    return [{"number": r[0], "issued_at": r[1]} for r in rows]


def db_reset_queue(reset_numbers=False):
    conn = _conn(); c = conn.cursor()
    c.execute('DELETE FROM tickets')
    conn.commit(); conn.close()
    if reset_numbers:
        set_meta('last_number', 0)


# ═══════════════════════════════════════════════════════════
#                    وضعیت آنلاین
# ═══════════════════════════════════════════════════════════
online_sessions = {}


def count_online(role):
    return sum(1 for s in online_sessions.values() if s['role'] == role)


def online_counter_ids():
    return {s['counter_id'] for s in online_sessions.values()
            if s['role'] == 'counter' and s['counter_id'] is not None}


def build_stats():
    waiting, serving, done, counters_info = db_stats()
    online_cids = online_counter_ids()
    for c in counters_info:
        c['is_online'] = c['id'] in online_cids
    return {
        "waiting_count": waiting, "serving_count": serving, "served_total": done,
        "counters": counters_info,
        "online_receptions": count_online('reception'),
        "online_announcers": count_online('announcer'),
        "online_counters": count_online('counter'),
        "waiting_list": db_recent_waiting(),
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%Y/%m/%d"),
    }


def broadcast_stats():
    socketio.emit('stats_update', build_stats(), room='global')


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
                           counters=db_all_counters(active_only=True), uni=CONFIG['university'])


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
                           counter_name=cinfo['name'], uni=CONFIG['university'])


@app.route('/announcer')
def announcer_dashboard():
    if session.get('role') != 'announcer':
        return redirect(url_for('login'))
    return render_template('announcer.html', name=session['name'],
                           counters=db_all_counters(active_only=True), uni=CONFIG['university'])


@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('admin.html',
                           name=session['name'],
                           uni=CONFIG['university'],
                           users=db_all_users(),
                           counters=db_all_counters(),
                           stats=build_stats())


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/stats')
def api_stats():
    return jsonify(build_stats())


# ═══════════════════════════════════════════════════════════
#                    Socket.IO — کاربر
# ═══════════════════════════════════════════════════════════
@socketio.on('connect')
def on_connect():
    join_room('global')
    print(f'[CONNECT] {request.sid}')


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
    number = next_ticket_number()
    db_add_ticket(number)
    broadcast_stats()
    socketio.emit('ticket_issued', {"number": number}, room='global')
    emit('issue_ok', {'msg': f'🎫 نوبت {number} به صف اضافه شد'})


@socketio.on('call_next')
def on_call_next(data):
    counter_id = int(data['counter_id'])
    status, number = db_call_next(counter_id)
    if status == "OK":
        payload = {
            "counter_id": counter_id,
            "ticket": {"number": number},
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        socketio.emit('ticket_called', payload, room='global')
    elif status == "BUSY":
        current = db_get_serving(counter_id)
        emit('error_msg', {'msg': f'⚠️ هنوز نوبت {current[1]} رو تموم نکردی — اول «اتمام» بزن'})
    elif status == "EMPTY":
        emit('error_msg', {'msg': 'صف خالیه — لطفاً صبر کنید'})
    broadcast_stats()


@socketio.on('finish_current')
def on_finish(data):
    counter_id = int(data['counter_id'])
    ok = db_finish(counter_id)
    if ok:
        emit('finish_ok', {'msg': '✅ نوبت تموم شد'})
    else:
        emit('error_msg', {'msg': 'شما نوبت فعالی ندارید'})
    broadcast_stats()


# ═══════════════════════════════════════════════════════════
#                    Socket.IO — ادمین
# ═══════════════════════════════════════════════════════════
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
    db_reset_queue(reset_numbers=reset_numbers)
    broadcast_stats()
    msg = '🔄 صف و آمار ریست شد' + (' (شماره هم از 001)' if reset_numbers else '')
    emit('admin_ok', {'msg': msg})
    socketio.emit('admin_refresh', {}, room='global')


# ═══════════════════════════════════════════════════════════
init_db()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)