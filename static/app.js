// ═══════════════════════════════════════════════════════════
//                     تم
// ═══════════════════════════════════════════════════════════
function initTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeBtn(saved);
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    updateThemeBtn(next);
}
function updateThemeBtn(theme) {
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}
(function earlyTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
})();

// ═══════════════════════════════════════════════════════════
//                صدا — صف اعلان قفل‌دار
// ═══════════════════════════════════════════════════════════
let ttsEnabled = localStorage.getItem('ttsEnabled') !== 'false';
let audioUnlocked = false;

function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        if (ctx.state === 'suspended') ctx.resume();
        console.log('🔓 Audio unlocked');
    } catch (e) {}
}
document.addEventListener('click', unlockAudio, { once: true });
document.addEventListener('touchstart', unlockAudio, { once: true });
document.addEventListener('keydown', unlockAudio, { once: true });

// ─── صف اعلان ───
let announceQueue = [];
let isAnnouncing = false;

function enqueueAnnounce(number, counterId) {
    if (!ttsEnabled) return;
    announceQueue.push({ number, counterId });
    updateAnnounceQueueUI();
    processAnnounceQueue();
}

async function processAnnounceQueue() {
    if (isAnnouncing) return;
    if (announceQueue.length === 0) return;

    isAnnouncing = true;
    while (announceQueue.length > 0) {
        const item = announceQueue.shift();
        updateAnnounceQueueUI();

        // پخش دو بار — هر بار کامل صبر کن
        await playTTS(item.number, item.counterId);
        await sleep(1000);
        await playTTS(item.number, item.counterId);

        // مکث بین دو نوبت مختلف
        if (announceQueue.length > 0) {
            await sleep(700);
        }
    }
    isAnnouncing = false;
}

function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
}

function playTTS(number, counterId) {
    return new Promise((resolve) => {
        const url = `/tts/${number}/${counterId}`;
        const audio = new Audio(url);
        let resolved = false;
        const done = () => { if (!resolved) { resolved = true; resolve(); } };
        audio.onended = done;
        audio.onerror = (e) => {
            console.warn('TTS error:', e);
            done();
        };
        audio.play().catch(e => {
            console.warn('Play blocked:', e);
            done();
        });
        setTimeout(done, 10000);
    });
}

function updateAnnounceQueueUI() {
    const el = document.getElementById('announceQueueCount');
    if (el) el.textContent = toPersianDigits(String(announceQueue.length));
    const box = document.getElementById('announceQueueBox');
    if (box) {
        if (announceQueue.length > 0) box.classList.remove('hidden');
        else box.classList.add('hidden');
    }
}

function toggleTTS() {
    ttsEnabled = !ttsEnabled;
    localStorage.setItem('ttsEnabled', ttsEnabled);
    toast(ttsEnabled ? '🔊 صدا فعال شد' : '🔇 صدا خاموش شد', 'ok');
    updateTTSBtn();
    if (!ttsEnabled) {
        announceQueue = [];
        updateAnnounceQueueUI();
    }
}

function updateTTSBtn() {
    const btn = document.getElementById('ttsToggle');
    if (btn) btn.textContent = ttsEnabled ? '🔊' : '🔇';
}

function testAnnounce() {
    unlockAudio();
    toast('🔊 در حال تست صدا...', 'ok');
    enqueueAnnounce('001', '1');
}

// ═══════════════════════════════════════════════════════════
//                     Socket.IO
// ═══════════════════════════════════════════════════════════
const socket = io({
    transports: ['websocket'],
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    pingInterval: 25000,
    pingTimeout: 60000,
});

let myRole = null;
let myCounterId = null;

socket.on('connect', () => {
    console.log('✅ Socket connected:', socket.id);
    if (myRole) {
        socket.emit('register_role', { role: myRole, counter_id: myCounterId });
    }
});
socket.on('disconnect', () => console.log('❌ Socket disconnected'));

function registerAs(role, counterId = null) {
    myRole = role;
    myCounterId = counterId;
    if (socket.connected) {
        socket.emit('register_role', { role, counter_id: counterId });
    }
}

// ═══════════════════════════════════════════════════════════
//                     ابزارهای کمکی
// ═══════════════════════════════════════════════════════════
function startClock() {
    const el = document.getElementById('liveClock');
    if (!el) return;
    const tick = () => { el.textContent = new Date().toLocaleTimeString('fa-IR'); };
    tick();
    setInterval(tick, 1000);
}

function toast(msg, type = 'ok') {
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3200);
}

function toPersianDigits(str) {
    const map = { '0':'۰','1':'۱','2':'۲','3':'۳','4':'۴','5':'۵','6':'۶','7':'۷','8':'۸','9':'۹' };
    return String(str).split('').map(c => map[c] || c).join('');
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) el.textContent = toPersianDigits(String(val));
}

// ═══════════════════════════════════════════════════════════
//                 دریافت آمار
// ═══════════════════════════════════════════════════════════
function renderStats(s) {
    setText('waiting_count', s.waiting_count);
    setText('serving_count', s.serving_count);
    setText('served_total', s.served_total);
    setText('online_reception', s.online_receptions);
    setText('online_announcer', s.online_announcers);
    setText('online_counter', s.online_counters);

    const container = document.getElementById('countersList');
    if (container) {
        container.innerHTML = s.counters.map(c => `
            <tr>
              <td class="font-bold">${toPersianDigits(c.id)}</td>
              <td>${c.name}</td>
              <td>
                <span class="pulse-dot ${c.is_online ? '' : 'offline'}"></span>
                ${c.is_online ? 'آنلاین' : 'آفلاین'}
              </td>
              <td class="font-bold text-lg text-blue-accent">
                ${c.current_ticket ? toPersianDigits(c.current_ticket) : '—'}
              </td>
            </tr>
        `).join('');
    }

    const qList = document.getElementById('queueList');
    if (qList) {
        if (!s.waiting_list.length) {
            qList.innerHTML = '<div class="text-muted text-center py-6">صف خالیه ✅</div>';
        } else {
            qList.innerHTML = s.waiting_list.map((t, i) => `
                <div class="flex justify-between items-center p-2 rounded-lg mb-1"
                     style="background: var(--surface-hover)">
                    <span class="text-muted text-xs w-6">${toPersianDigits(i + 1)}.</span>
                    <span class="font-bold text-lg flex-1 text-center text-yellow-accent">
                        ${toPersianDigits(t.number)}
                    </span>
                    <span class="text-muted text-xs">${toPersianDigits(t.issued_at.slice(11,16))}</span>
                </div>
            `).join('');
        }
    }

    if (window.myCounterId && s.counters) {
        const me = s.counters.find(c => c.id === window.myCounterId);
        const el = document.getElementById('myCurrent');
        const sub = document.getElementById('myCurrentSub');
        if (el) {
            const hasTicket = me && me.current_ticket;
            el.textContent = hasTicket ? toPersianDigits(me.current_ticket) : '—';
            el.classList.toggle('idle', !hasTicket);
            if (sub) sub.textContent = hasTicket ? 'مشتری در حال سرویس است' : 'در انتظار نوبت...';
            const callBtn = document.getElementById('callNextBtn');
            const finishBtn = document.getElementById('finishBtn');
            if (callBtn) callBtn.disabled = !!hasTicket;
            if (finishBtn) finishBtn.disabled = !hasTicket;
        }
    }

    const bigCurrent = document.getElementById('bigCurrent');
    if (bigCurrent) {
        const active = s.counters.find(c => c.current_ticket);
        bigCurrent.textContent = active ? toPersianDigits(active.current_ticket) : '—';
        bigCurrent.classList.toggle('idle', !active);
        const bigCounter = document.getElementById('bigCounter');
        if (bigCounter) bigCounter.textContent = active ? `باجه شماره ${toPersianDigits(active.id)}` : '';
    }
}
window.renderStats = renderStats;

socket.on('stats_update', (s) => {
    renderStats(s);
});

// ═══════════════════════════════════════════════════════════
//              وقتی نوبتی صدا زده شد
// ═══════════════════════════════════════════════════════════
socket.on('ticket_called', (data) => {
    const num = toPersianDigits(data.ticket.number);
    const cid = toPersianDigits(data.counter_id);
    toast(`🔔 نوبت ${num} → باجه ${cid}`, 'ok');

    // ✅ فقط پذیرش صدا رو پخش کنه (باجه و مجری فقط Toast)
    if (myRole === 'reception') {
        enqueueAnnounce(data.ticket.number, data.counter_id);
    }

    // مجری: تاریخچه اعلان‌ها
    if (myRole === 'announcer') {
        addToAnnounceHistory(data.ticket.number, data.counter_id, data.timestamp);
    }
});

function addToAnnounceHistory(number, counterId, timestamp) {
    const list = document.getElementById('announceHistory');
    if (!list) return;
    if (list.children.length === 1 && list.children[0].classList.contains('text-muted')) {
        list.innerHTML = '';
    }
    const item = document.createElement('div');
    item.className = 'flex justify-between items-center p-2 rounded-lg mb-1 text-sm';
    item.style.background = 'var(--surface-hover)';
    item.innerHTML = `
        <span class="font-bold text-blue-accent text-lg">${toPersianDigits(number)}</span>
        <span class="text-muted">باجه ${toPersianDigits(counterId)}</span>
        <span class="text-muted text-xs">${toPersianDigits(timestamp || '')}</span>
    `;
    list.prepend(item);
    while (list.children.length > 15) {
        list.removeChild(list.lastChild);
    }
}

socket.on('finish_ok', (d) => toast(d.msg, 'ok'));
socket.on('error_msg', (d) => toast(d.msg, 'err'));

// ═══════════════════════════════════════════════════════════
//                     راه‌اندازی
// ═══════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    startClock();
    updateThemeBtn(localStorage.getItem('theme') || 'dark');
    updateTTSBtn();

    const themeBtn = document.getElementById('themeToggle');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

    const ttsBtn = document.getElementById('ttsToggle');
    if (ttsBtn) ttsBtn.addEventListener('click', toggleTTS);

    const testBtn = document.getElementById('testAudioBtn');
    if (testBtn) testBtn.addEventListener('click', testAnnounce);
});