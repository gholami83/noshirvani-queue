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
//                صدای اعلام — نسخه بانکی
// ═══════════════════════════════════════════════════════════
let ttsEnabled = localStorage.getItem('ttsEnabled') !== 'false';
let audioUnlocked = false;
let faVoice = null;
let announceQueue = [];   // صف اعلان
let isAnnouncing = false;

// --- کشف صداها ---
function loadVoices() {
    if (!('speechSynthesis' in window)) return;
    const voices = window.speechSynthesis.getVoices();
    faVoice =
        voices.find(v => v.lang === 'fa-IR') ||
        voices.find(v => v.lang && v.lang.startsWith('fa')) ||
        voices.find(v => v.lang === 'ar-SA') ||
        voices.find(v => v.lang && v.lang.startsWith('ar')) ||
        null;
    console.log('🎙️ TTS:', voices.length, 'صدا | فارسی:', faVoice ? faVoice.name : '❌ نصب نیست');
}
if ('speechSynthesis' in window) {
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
}

// --- آنلاک کردن صدا با اولین کلیک ---
function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    // یه utterance خالی که مرورگر رو آنلاک کنه
    try {
        const u = new SpeechSynthesisUtterance(' ');
        u.volume = 0.01;
        window.speechSynthesis.speak(u);
        console.log('🔓 Audio unlocked');
    } catch (e) {}
    // دینگ خالی هم آنلاک کن
    playDing(0.01);
}
document.addEventListener('click', unlockAudio, { once: true });
document.addEventListener('touchstart', unlockAudio, { once: true });
document.addEventListener('keydown', unlockAudio, { once: true });

// --- دینگ بانکی (Web Audio API — بدون فایل) ---
let audioCtx = null;
function getAudioCtx() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
}
function playDing(volume = 0.4) {
    try {
        const ctx = getAudioCtx();
        if (ctx.state === 'suspended') ctx.resume();
        const now = ctx.currentTime;

        // دو بپ: یه دینگ دو نتی مثل بانک
        [880, 1320].forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0, now + i * 0.15);
            gain.gain.linearRampToValueAtTime(volume, now + i * 0.15 + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.15 + 0.35);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + i * 0.15);
            osc.stop(now + i * 0.15 + 0.4);
        });
    } catch (e) {
        console.warn('Ding failed:', e);
    }
}

// --- تبدیل عدد به فارسی ---
function toPersianDigits(str) {
    const map = { '0':'۰','1':'۱','2':'۲','3':'۳','4':'۴','5':'۵','6':'۶','7':'۷','8':'۸','9':'۹' };
    return String(str).split('').map(c => map[c] || c).join('');
}

// --- گفتن یک متن با Promise ---
function speak(text) {
    return new Promise((resolve) => {
        if (!('speechSynthesis' in window)) {
            console.warn('speechSynthesis not supported');
            return resolve();
        }
        const utter = new SpeechSynthesisUtterance(text);
        utter.lang = 'fa-IR';
        utter.rate = 0.85;
        utter.pitch = 1.0;
        utter.volume = 1.0;
        if (faVoice) utter.voice = faVoice;

        utter.onend = () => resolve();
        utter.onerror = (e) => {
            console.warn('Speech error:', e);
            resolve();
        };

        window.speechSynthesis.speak(utter);
        console.log('🗣️ Speaking:', text);
    });
}

// --- صف اعلان (خیلی مهم برای دو نوبت پشت سر هم) ---
function enqueueAnnounce(number, counterId) {
    if (!ttsEnabled) return;
    const num = toPersianDigits(String(number));
    const cid = toPersianDigits(String(counterId));
    announceQueue.push({ number: num, counterId: cid });
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

        // ۱. دینگ
        playDing(0.5);
        await new Promise(r => setTimeout(r, 500)); // فاصله بعد از دینگ

        // ۲. اعلام صدا
        const text = `نوبت ${item.number}، باجه شماره ${item.counterId}`;
        await speak(text);

        // ۳. فاصله بین دو اعلام
        await new Promise(r => setTimeout(r, 400));
    }
    isAnnouncing = false;
}

function updateAnnounceQueueUI() {
    const el = document.getElementById('announceQueueCount');
    if (el) el.textContent = toPersianDigits(String(announceQueue.length));
    const box = document.getElementById('announceQueueBox');
    if (box) {
        if (announceQueue.length > 0) {
            box.classList.remove('hidden');
        } else {
            box.classList.add('hidden');
        }
    }
}

function toggleTTS() {
    ttsEnabled = !ttsEnabled;
    localStorage.setItem('ttsEnabled', ttsEnabled);
    toast(ttsEnabled ? '🔊 صدا فعال شد' : '🔇 صدا خاموش شد', 'ok');
    updateTTSBtn();
    if (!ttsEnabled) {
        window.speechSynthesis.cancel();
        announceQueue = [];
        isAnnouncing = false;
    }
}

function updateTTSBtn() {
    const btn = document.getElementById('ttsToggle');
    if (btn) btn.textContent = ttsEnabled ? '🔊' : '🔇';
}

// تست صدا از پنل مجری
async function testAnnounce() {
    unlockAudio();
    toast('🔊 در حال تست صدا...', 'ok');
    playDing(0.5);
    await new Promise(r => setTimeout(r, 500));
    if (faVoice) {
        await speak('تست صدای اعلام نوبت');
    } else {
        toast('⚠️ صدای فارسی نصب نیست — از تنظیمات ویندوز فارسی اضافه کن', 'err');
        await speak('Test announcement');
    }
}

// ═══════════════════════════════════════════════════════════
//                     Socket.IO
// ═══════════════════════════════════════════════════════════
const socket = io({
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
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

// ═══════════════════════════════════════════════════════════
//                 دریافت آمار — همه نقش‌ها
// ═══════════════════════════════════════════════════════════
socket.on('stats_update', (s) => {
    setText('waiting_count', s.waiting_count);
    setText('serving_count', s.serving_count);
    setText('served_total', s.served_total);
    setText('online_reception', s.online_receptions);
    setText('online_announcer', s.online_announcers);
    setText('online_counter', s.online_counters);

    // جدول باجه‌ها
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

    // صف در انتظار
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

    // باجه: نوبت فعال من
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

    // مجری: نوبت بزرگ
    const bigCurrent = document.getElementById('bigCurrent');
    if (bigCurrent) {
        const active = s.counters.find(c => c.current_ticket);
        bigCurrent.textContent = active ? toPersianDigits(active.current_ticket) : '—';
        bigCurrent.classList.toggle('idle', !active);
        const bigCounter = document.getElementById('bigCounter');
        if (bigCounter) bigCounter.textContent = active ? `باجه شماره ${toPersianDigits(active.id)}` : '';
    }
});

// ═══════════════════════════════════════════════════════════
//              وقتی نوبتی صدا زده شد — مهم‌ترین بخش
// ═══════════════════════════════════════════════════════════
socket.on('ticket_called', (data) => {
    const num = toPersianDigits(data.ticket.number);
    const cid = toPersianDigits(data.counter_id);
    toast(`🔔 نوبت ${num} → باجه ${cid}`, 'ok');

    // مجری و باجه: صدا + صف اعلان
    if (myRole === 'announcer' || myRole === 'counter') {
        enqueueAnnounce(data.ticket.number, data.counter_id);
    }

    // مجری: اضافه به تاریخچه اعلان‌ها
    if (myRole === 'announcer') {
        addToAnnounceHistory(data.ticket.number, data.counter_id, data.timestamp);
    }
});

function addToAnnounceHistory(number, counterId, timestamp) {
    const list = document.getElementById('announceHistory');
    if (!list) return;
    const item = document.createElement('div');
    item.className = 'flex justify-between items-center p-2 rounded-lg mb-1 text-sm';
    item.style.background = 'var(--surface-hover)';
    item.innerHTML = `
        <span class="font-bold text-blue-accent text-lg">${toPersianDigits(number)}</span>
        <span class="text-muted">باجه ${toPersianDigits(counterId)}</span>
        <span class="text-muted text-xs">${toPersianDigits(timestamp)}</span>
    `;
    list.prepend(item);
    // فقط ۱۵ تای آخر
    while (list.children.length > 15) {
        list.removeChild(list.lastChild);
    }
}

socket.on('finish_ok', (d) => toast(d.msg, 'ok'));
socket.on('error_msg', (d) => toast(d.msg, 'err'));

function setText(id, val) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) el.textContent = toPersianDigits(String(val));
}

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