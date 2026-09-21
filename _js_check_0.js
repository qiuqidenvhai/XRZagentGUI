
    // 优先使用 window.XIAOZHANG_API，否则回退到默认。
    // 注意：直接双击打开 html 时 location.origin === 'file://'，它是 truthy 值，
    // 会把兜底地址顶掉，导致所有请求变成 file:///xxx 而被浏览器拒绝。
    // 因此遇到 file: 协议必须当作「没有 origin」处理。
    const _ORIGIN = (window.location.protocol === 'file:') ? '' : window.location.origin;
    const API = window.XIAOZHANG_API || _ORIGIN || 'http://127.0.0.1:8888';
    console.log('[GUI] API 地址:', API);
const msgDiv = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');

let connected = false;
let sending = false;
let sendStartTime = 0;         // 上一次发送的开始时刻（用于卡死检测）
let pendingAttachments = [];   // 待发送附件的绝对路径
let SESSIONS = [];              // 多会话列表（来自 session_manager）
let CURRENT_SESSION = null;    // 当前会话 id

inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

function handleKey(e) {
    // 【交互约定】与主流聊天软件一致：Enter 直接发送，Shift+Enter 换行。
    // 旧实现只认 Ctrl+Enter，普通用户按 Enter 只会插入换行符 —— 表现为
    // 「我发的消息没发出去 / 面板上不显示」，是用户反复投诉的那条。
    if (e.key !== 'Enter') return;
    // 中文输入法组字过程中（composition）的 Enter 是在选词，不能当发送
    if (e.isComposing || e.keyCode === 229) return;
    if (e.shiftKey) return;              // Shift+Enter = 换行
    e.preventDefault();
    sendInput();
}

function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── 把环境层「删除/写入拦截」的内部噪音从展示文本里剥掉 ──
// 生成 PPT/Word 等会清理 50+ 个中间文件，触发沙箱的 safe-delete 批量确认
// （SAFE_DELETE_BULK_CONFIRM_REQUIRED），原始 JSON 会混进工具输出/回复。
// 这是【环境守卫】，产物其实已生成成功——不该把这段吓人的内部报文甩给终端用户。
function stripNoise(text) {
    if (!text || typeof text !== 'string') return text;
    let s = text;
    // 删掉形如 [safe-delete][SAFE_DELETE_...] {...} 的整行/整段
    s = s.replace(/\[safe-delete\]\[SAFE_DELETE_[A-Z_]+\][^\n]*/g, '');
    s = s.replace(/\[safe-delete\][^\n]*/g, '');
    // 删掉裸的 SAFE_DELETE_* JSON 块
    s = s.replace(/SAFE_DELETE_[A-Z_]+\s*[:{]?/g, '');
    // 删掉把「写入被拦截 / 删除被拒绝」这类内部措辞换成友好说法
    s = s.replace(/(文件写入|对话索引写入)被(拦截|拒绝)[^\n。；;]*/g, '');
    s = s.replace(/删除被拦截[^\n。；;]*/g, '');
    // 清掉因此产生的多余空行
    s = s.replace(/\n{3,}/g, '\n\n').trim();
    return s;
}

// ── 任务列表（WorkBuddy 风格）──
let TASKS = [];
let activeTaskId = null;

function renderTaskList() {
    const el = document.getElementById('taskList');
    if (!el) return;
    const running = TASKS.filter(t => t.status === 'running');
    const done = TASKS.filter(t => t.status !== 'running');
    if (running.length === 0 && done.length === 0) {
        el.innerHTML = '<div class="task-empty">暂无运行任务</div>';
        return;
    }
    el.innerHTML = '';
    const all = [...running, ...done];
    all.forEach(t => {
        const div = document.createElement('div');
        const icon = t.status === 'running' ? '🔄' : t.status === 'done' ? '✅' : '❌';
        const cls = t.status === 'running' ? 'running' : t.status === 'done' ? 'done' : 'failed';
        div.className = 'task-item ' + cls;
        div.title = t.title;
        div.onclick = () => selectTask(t.id);
        div.innerHTML = `
            <span class="task-icon">${icon}</span>
            <div class="task-info">
                <div class="task-title">${escapeHtml(t.title || t.id)}</div>
                <div class="task-meta"><span>${t.status === 'running' ? '运行中' : '已完成'}</span><span>${t.platform || ''}</span></div>
            </div>
            <div class="task-actions">
                ${t.status !== 'running' ? `<button class="task-btn delete" onclick="event.stopPropagation();removeTask('${t.id}')" title="删除">✕</button>` : ''}
            </div>
        `;
        el.appendChild(div);
    });
}

function addTask(id, title, platform) {
    TASKS = TASKS.filter(t => t.id !== id);
    TASKS.push({ id, title: title || id, platform: platform || CURRENT_PLATFORM, status: 'running', created_at: Date.now() });
    renderTaskList();
}

function completeTask(id, status = 'done') {
    const t = TASKS.find(t => t.id === id);
    if (t) t.status = status;
    renderTaskList();
    // 【产物显示修复】任务结束就拉「该任务自己的」产物列表。
    // 关键：必须带上 task id 作为会话范围 —— 否则会把别的任务的产物混进来。
    if (status === 'done' || status === 'failed') {
        if (typeof loadAttachments === 'function') loadAttachments(id);
    }
}

async function removeTask(id) {
    if (!confirm('确定删除这个任务（对话记录）吗？\n删除后无法恢复。')) return;
    TASKS = TASKS.filter(t => t.id !== id);
    renderTaskList();
    if (TRANSCRIPTS[id]) { delete TRANSCRIPTS[id]; saveTranscripts(); }
    if (CURRENT_TASK_ID === id) CURRENT_TASK_ID = null;
    // 【核心修复】旧实现只从前端数组里删（刷新/重启就复活），是「假删除」。
    // 真正调后端 DELETE /conversations/<id>，把后端对话索引 + 文件一起删掉。
    try {
        const resp = await fetch(API + '/conversations/' + encodeURIComponent(id), { method: 'DELETE' });
        const data = await safeJson(resp);
        if (data.type === 'ok') {
            addMsg('🗑 已删除任务 ' + id, 'system');
            setTimeout(() => { try { loadHistory(); } catch (e) {} }, 300);
        } else {
            addMsg('删除任务: ' + ((data.text) || '未知错误'), 'error');
        }
    } catch (e) {
        addMsg('删除任务失败: ' + e.message, 'error');
    }
}

function selectTask(id) {
    // 点任务 = 切到该任务的上下文（实时转录里一般已有完整过程）
    const t = TASKS.find(x => x.id === id);
    openHistoryTask(id, null, t ? t.title : id, t ? t.platform : '', t && t.created_at
        ? new Date(t.created_at).toLocaleString('zh-CN') : '');
}

// ══════════════════════════════════════════════════════════════
// 历史任务视图：点哪个任务就显示哪个任务的上下文
// ══════════════════════════════════════════════════════════════
// 每个任务的过程（用户消息 / 思考 / 工具 / 最终回复）按 task_id 归档并持久化，
// 因此重启后仍能逐项回看；更早的任务（没有转录）回退读会话 JSON 文件。
let TRANSCRIPTS = {};
let LAST_USER_TEXT = '';      // 待归属到下一个 task_started 的用户输入
let CURRENT_TASK_ID = null;   // 当前正在跑的任务
let HIST_VIEW = null;         // null = 实时视图；否则 {taskId}
let NEWING = false;           // 「新建对话」进行中：防连点
let LIVE_DOM_CACHE = null;    // 进入历史视图前的实时 DOM，用于返回
const TR_KEY = 'xrz_transcripts_v1';

function loadTranscripts() {
    try { TRANSCRIPTS = JSON.parse(localStorage.getItem(TR_KEY) || '{}') || {}; }
    catch (e) { TRANSCRIPTS = {}; }
}
function saveTranscripts() {
    try {
        const keys = Object.keys(TRANSCRIPTS);
        if (keys.length > 60) keys.slice(0, keys.length - 60).forEach(k => delete TRANSCRIPTS[k]);
        localStorage.setItem(TR_KEY, JSON.stringify(TRANSCRIPTS));
    } catch (e) { /* 配额满时静默降级，不影响主流程 */ }
}
function recordStep(tid, kind, text) {
    if (!tid || !text) return;
    if (!TRANSCRIPTS[tid]) TRANSCRIPTS[tid] = [];
    const steps = TRANSCRIPTS[tid];
    // 同一工具相邻输出合并，避免刷屏
    const last = steps[steps.length - 1];
    if (last && last.kind === kind && kind === 'thinking') last.text = text;
    else steps.push({ kind, text: String(text).slice(0, 20000), ts: Date.now() });
    if (steps.length > 200) TRANSCRIPTS[tid] = steps.slice(-200);
    saveTranscripts();
}
// SSE → 转录归档
function stripProtocol(text) {
    // 去掉 @@@@ {...} @@@@ 形式的工具协议块（元宝等平台回复被截断时也可能只剩开头标记）
    let s = String(text || '');
    s = s.replace(/@@@@[\s\S]*?@@@@/g, '');
    s = s.replace(/@@@@[\s\S]*$/, '');   // 只有开头 @@@@ 的截断残块
    return s.trim();
}
function archiveEvent(etype, data) {
    const tid = CURRENT_TASK_ID;
    if (etype === 'task_started') {
        const t = data || {};
        CURRENT_TASK_ID = String(t.task_id || '');
        if (LAST_USER_TEXT) { recordStep(CURRENT_TASK_ID, 'user', LAST_USER_TEXT); LAST_USER_TEXT = ''; }
        return;
    }
    if (!tid) return;
    // 历史回看里不该露出模型用的 @@@@ 工具协议原文（那是给程序看的），
    // 只保留它真正说给用户听的那部分；整段都是协议的话直接不记这一步。
    const text = stripProtocol(data && data.text ? data.text : '');
    if (!text && etype !== 'tool_start' && etype !== 'tool_end') return;
    if (etype === 'ai_thinking') recordStep(tid, 'thinking', text);
    else if (etype === 'thinking') recordStep(tid, 'plan', text);
    else if (etype === 'tool_start') recordStep(tid, 'tool', `🔧 调用 ${(data && data.tool) || ''}`);
    else if (etype === 'tool_end') {
        const st = (data && data.status) || '';
        const out = (data && data.output) || '';
        recordStep(tid, 'tool', `🔧 ${(data && data.tool) || ''} → ${st}\n${String(out).slice(0, 3000)}`);
    } else if (etype === 'ai_final_reply') recordStep(tid, 'ai', text);
    else if (etype === 'question') recordStep(tid, 'question', text);
}
function histThinkBlock(title) {
    const wrap = document.createElement('div');
    wrap.className = 'thinking-block done';
    const head = document.createElement('div');
    head.className = 'think-head';
    head.innerHTML = `<span class="dot"></span><span>${title}</span>`
        + '<span style="margin-left:auto;font-size:11px;opacity:.6">点击折叠</span>';
    const body = document.createElement('div');
    body.className = 'think-body';
    head.onclick = () => wrap.classList.toggle('collapsed');
    wrap.appendChild(head); wrap.appendChild(body);
    msgDiv.appendChild(wrap);
    return body;
}
function pushHistBlock(body, text) {
    const p = document.createElement('div');
    p.className = 'think-step';
    p.textContent = text;
    body.appendChild(p);
}
function histNote(text) {
    const el = document.createElement('div');
    el.className = 'hist-note';
    el.textContent = text;
    msgDiv.appendChild(el);
}
// 把某任务的过程渲染进消息区
function renderTranscript(taskId, steps, note) {
    msgDiv.innerHTML = '';
    const banner = document.createElement('div');
    banner.className = 'hist-banner';
    banner.innerHTML = '<span class="hb-tag">历史任务</span>'
        + '<span class="hb-title"></span>'
        + '<span class="hb-meta"></span>'
        + '<span class="hb-back">← 返回当前对话</span>';
    banner.querySelector('.hb-title').textContent = (HIST_VIEW && HIST_VIEW.title) || '(无标题)';
    banner.querySelector('.hb-meta').textContent =
        ((HIST_VIEW && HIST_VIEW.platform) || '') + ' · ' + ((HIST_VIEW && HIST_VIEW.time) || '')
        + ' · ' + taskId;
    banner.querySelector('.hb-back').onclick = closeHistoryView;
    msgDiv.appendChild(banner);
    if (note) histNote(note);
    if (!steps || !steps.length) {
        histNote('这条任务没有可回看的过程记录（可能是更早的历史，或当时未捕获到内容）。');
        return;
    }
    let thinkBody = null;
    for (const s of steps) {
        if (s.kind === 'user') { thinkBody = null; addMsg(s.text, 'user'); }
        else if (s.kind === 'ai') { thinkBody = null; addMsg(s.text, 'ai'); }
        else if (s.kind === 'question') { thinkBody = null; addMsg('❓ ' + s.text, 'question'); }
        else if (s.kind === 'thinking' || s.kind === 'plan') {
            const isModel = s.kind === 'thinking';
            if (!thinkBody || thinkBody._histKind !== s.kind) {
                thinkBody = histThinkBlock(isModel ? '🧠 模型推理（深度思考）' : '🧠 思考过程（Agent 计划）');
                thinkBody._histKind = s.kind;
            }
            pushHistBlock(thinkBody, s.text);
        } else if (s.kind === 'tool') {
            const pre = document.createElement('div');
            pre.className = 'msg msg-log';
            pre.style.whiteSpace = 'pre-wrap';
            pre.textContent = s.text;
            msgDiv.appendChild(pre);
            thinkBody = null;
        }
    }
    msgDiv.scrollTop = 0;
}
async function openHistoryTask(id, file, title, platform, time) {
    if (!id) return;
    if (HIST_VIEW) { closeHistoryView(); }   // 先回到实时视图，避免视图套娃
    const steps = TRANSCRIPTS[id];
    HIST_VIEW = { taskId: id, title: title || id, platform: platform || '', time: time || '' };
    LIVE_DOM_CACHE = msgDiv.innerHTML;
    if (steps && steps.length) {
        renderTranscript(id, steps, null);
    } else if (file) {
        // 旧任务没有转录 → 回退读会话 JSON（只有用户/AI 文本，无思考）
        try {
            const r = await fetch(API + '/preview?path=' + encodeURIComponent(file),
                { signal: AbortSignal.timeout(10000) });
            const d = await safeJson(r);
            const raw = d && d.raw ? d.raw : '';
            const conv = raw ? JSON.parse(raw) : null;
            const msgs = (conv && conv.messages) || [];
            const conv2 = msgs.map(m => ({
                kind: m.role === 'user' ? 'user' : 'ai',
                text: m.content || ''
            }));
            renderTranscript(id, conv2,
                '该任务由会话文件还原：仅含用户与 AI 的文本记录，'
                + '当时未单独保存思考过程（思考过程从本次升级起按任务归档，之后的任务都可回看）。');
        } catch (e) {
            renderTranscript(id, [], '读取会话文件失败：' + e.message);
        }
    } else {
        renderTranscript(id, [], null);
    }
    // 高亮
    document.querySelectorAll('.task-item').forEach(el => el.classList.remove('active'));
    const el = document.querySelector(`.task-item[onclick*="${id}"]`);
    if (el) el.classList.add('active');
    activeTaskId = id;
    // 侧边栏产物跟随所选任务（任务私有上下文）
    bindFileScope(id);
}
function closeHistoryView() {
    if (!HIST_VIEW) {
        // 【2026-09-21 修复】即使 HIST_VIEW 已是 null，也要把缓存清掉。
        // 否则残留的 LIVE_DOM_CACHE（回看历史前抓的旧 HTML 快照）会在之后的
        // 某次 closeHistoryView / resetChatView 里把「已清空的实时视图」
        // 覆盖回旧内容，表现为：用户发的消息气泡莫名其妙消失 / 空对话里冒出旧消息。
        LIVE_DOM_CACHE = null;
        return;
    }
    HIST_VIEW = null;
    if (LIVE_DOM_CACHE !== null) {
        // 只有「当前消息区确实处于历史回看态」时才恢复快照。
        // 若消息区已经被 resetChatView / 新一轮对话重新写过（含 .welcome 或
        // 只剩系统提示），恢复旧快照就是错的 —— 直接丢弃缓存。
        const inHistMode = !!msgDiv.querySelector('.hist-banner');
        if (inHistMode) {
            msgDiv.innerHTML = LIVE_DOM_CACHE;
        }
        LIVE_DOM_CACHE = null;
    }
    msgDiv.scrollTop = msgDiv.scrollHeight;
    activeTaskId = null;
    document.querySelectorAll('.task-item').forEach(el => el.classList.remove('active'));
    // 回到实时视图 = 没有选中历史任务 → 侧边栏无产物
    bindFileScope(null);
    resetThinking();
}

// ── 思考过程块（流式更新同一块，避免刷屏）──
let currentThinkingBlock = null;     // 平台深度思考(R1) 推理流
let currentThinkingBody = null;
let currentPlanBlock = null;         // Commander 每轮计划/推理（累积展示）
let currentPlanBody = null;

function resetThinking() {
    currentThinkingBlock = null;
    currentThinkingBody = null;
    currentPlanBlock = null;
    currentPlanBody = null;
}

function showThinking(text) {
    if (!text) return;
    if (!currentThinkingBlock) {
        const wrap = document.createElement('div');
        wrap.className = 'thinking-block';
        const head = document.createElement('div');
        head.className = 'think-head';
        head.innerHTML = '<span class="dot"></span><span>🧠 模型推理（深度思考）</span><span style="margin-left:auto;font-size:11px;opacity:.6">点击折叠</span>';
        const body = document.createElement('div');
        body.className = 'think-body';
        head.onclick = () => wrap.classList.toggle('collapsed');
        wrap.appendChild(head);
        wrap.appendChild(body);
        const welcome = msgDiv.querySelector('.welcome');
        if (welcome) welcome.remove();
        msgDiv.appendChild(wrap);
        currentThinkingBlock = wrap;
        currentThinkingBody = body;
    }
    if (currentThinkingBody) {
        currentThinkingBody.textContent = stripNoise(text);
        msgDiv.scrollTop = msgDiv.scrollHeight;
    }
}

function endThinking() {
    if (currentThinkingBlock) currentThinkingBlock.classList.add('done');
}

// ── Agent 计划/思考过程块（每轮累积，展示 Commander 的逐步推理）──
function appendThinking(text) {
    if (!text) return;
    if (!currentPlanBlock) {
        const wrap = document.createElement('div');
        wrap.className = 'thinking-block';
        const head = document.createElement('div');
        head.className = 'think-head';
        head.innerHTML = '<span class="dot"></span><span>🧠 思考过程（Agent 计划）</span><span style="margin-left:auto;font-size:11px;opacity:.6">点击折叠</span>';
        const body = document.createElement('div');
        body.className = 'think-body';
        head.onclick = () => wrap.classList.toggle('collapsed');
        wrap.appendChild(head);
        wrap.appendChild(body);
        const welcome = msgDiv.querySelector('.welcome');
        if (welcome) welcome.remove();
        msgDiv.appendChild(wrap);
        currentPlanBlock = wrap;
        currentPlanBody = body;
    }
    // 每个思考片段作为独立段落追加，保留完整推理链（不覆盖上一步）
    const p = document.createElement('div');
    p.className = 'think-step';
    p.textContent = stripNoise(text);
    currentPlanBody.appendChild(p);
    msgDiv.scrollTop = msgDiv.scrollHeight;
}

function endPlan() {
    if (currentPlanBlock) currentPlanBlock.classList.add('done');
}

function addMsg(text, cls = 'system', isHtml) {
    if (cls === 'user') resetThinking();  // 新对话轮开始，重置上一轮的思考块
    // 剥掉「写入/删除被拦截」等环境内部噪音，避免把 safe-delete 守卫报文甩给用户
    if (!isHtml) text = stripNoise(text);
    const el = document.createElement('div');
    el.className = 'msg msg-' + cls;
    if (isHtml) {
        el.innerHTML = text;
    } else {
        el.textContent = text;
    }
    const welcome = msgDiv.querySelector('.welcome');
    if (welcome) welcome.remove();
    msgDiv.appendChild(el);
    msgDiv.scrollTop = msgDiv.scrollHeight;
}

function showTyping() {
    const el = document.createElement('div');
    el.className = 'typing';
    el.id = 'typing';
    el.innerHTML = '<span></span><span></span><span></span>';
    const welcome = msgDiv.querySelector('.welcome');
    if (welcome) welcome.remove();
    msgDiv.appendChild(el);
    msgDiv.scrollTop = msgDiv.scrollHeight;
}

function hideTyping() {
    const el = document.getElementById('typing');
    if (el) el.remove();
}

function setStatus(state) {
    statusDot.className = 'status-dot ' + state;
    if (state === 'online') { statusText.textContent = '已连接'; connected = true; sendBtn.disabled = false; stopBtn.style.display = 'none'; insertBtn.style.display = 'none'; }
    else if (state === 'busy') { statusText.textContent = '工作中...'; connected = true; sendBtn.disabled = true; stopBtn.style.display = 'flex'; insertBtn.style.display = 'flex'; }
    else { statusText.textContent = '未连接'; connected = false; sendBtn.disabled = true; stopBtn.style.display = 'none'; insertBtn.style.display = 'none'; }
}

// 中断当前任务
async function interruptTask() {
    try {
        const resp = await fetch(API + '/interrupt', { method: 'POST' });
        const data = await resp.json();
        if (data.type === 'ok') {
            addMsg('⏹ 已发送中断信号，等待当前工具完成后暂停', 'system');
            stopBtn.style.display = 'none';
            insertBtn.style.display = 'none';
            setStatus('online');
        }
    } catch(e) {
        addMsg('中断失败: ' + e.message, 'error');
    }
}

// 插话（加入队列，不等当前任务完成）
async function insertMessage() {
    const text = inputEl.value.trim();
    if (!text) {
        // 如果没有输入内容，弹出提示框
        const msg = prompt('输入插话内容：');
        if (!msg || !msg.trim()) return;
        await queueMessage(msg.trim());
    } else {
        await queueMessage(text);
        inputEl.value = '';
    }
}

async function queueMessage(message) {
    try {
        const resp = await fetch(API + '/message', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message})
        });
        const data = await resp.json();
        if (data.type === 'ok') {
            addMsg('💬 插话已入队（排队 #' + (data.queue_size || 1) + '）', 'system');
        }
    } catch(e) {
        addMsg('插话失败: ' + e.message, 'error');
    }
}

// 插入SSE对接
let eventSource = null;
function connectEvents() {
    if (eventSource) { eventSource.close(); }
    eventSource = new EventSource(API + '/events');
    eventSource.onopen = () => {
        console.log('Events connected');
        // 【修复】重连成功后恢复可发送状态（否则按钮会一直保持禁用）
        if (!sending) setStatus('online');
    };
    eventSource.onmessage = (ev) => {
        if (!ev.data) return;  // 跳过 keepalive / 空行
        try {
            const data = JSON.parse(ev.data);
            const etype = data.type || 'unknown';
            const edata = data.data;
            // 解析并渲染到GUI
            handleGuiEvent(etype, edata);
        } catch(e) {
            console.warn('SSE parse err:', e);
        }
    };
    eventSource.onerror = () => {
        console.log('Events reconnecting...');
        eventSource.close();
        // 【修复】后端失联时释放发送锁，避免 sending 永久卡死、按钮不可用
        if (sending) {
            sending = false;
            hideTyping();
            addMsg('⚠️ 与后端的事件连接已断开，本次任务可能未完成。\n'
                 + '发送锁已释放，可以重新发送。', 'error');
        }
        setStatus('offline');
        setTimeout(connectEvents, 3000);
    };
}

// 取当Commander于发于SSE事件
function handleGuiEvent(etype, data) {
    // 先归档到任务转录（历史视图靠它回看，不受当前显示模式影响）
    try { archiveEvent(etype, data); } catch (e) { console.warn('archive err', e); }
    // ── 子代理事件：聚合进子代理卡片（带 subagent_task_id 标记，subagent_manager 转发）──
    // 这些事件不再走普通工具卡/思考块，而是进专属子代理卡，用户能看到子代理内部多轮。
    if (data && data.subagent_task_id) {
        if (HIST_VIEW) return;   // 回看历史时不画实时子代理流（与现有 thinking/tool_end 一致）
        upsertSubagentCard(data, etype);
        return;
    }
    // 正在回看历史任务时，不要把这些实时事件画进历史视图里；
    // 但仍要维护任务列表与发送锁，保证后台任务状态正确。
    if (HIST_VIEW && (etype === 'ai_thinking' || etype === 'thinking'
                      || etype === 'ai_final_reply' || etype === 'tool_end'
                      || etype === 'question')) {
        if (etype === 'ai_final_reply') {
            sending = false;
            setStatus('online');
            const tid0 = (data && data.task_id) ? String(data.task_id) : null;
            if (tid0) completeTask(tid0, data && data.type === 'error' ? 'failed' : 'done');
        }
        return;
    }
    if (etype === 'task_started') {
        const tt = data || {};
        addTask(tt.task_id || '', tt.title || '', tt.platform || CURRENT_PLATFORM);
    }
    // 思考过程：实时渲染为可折叠的「思考过程」块
    if (etype === 'ai_thinking') {
        showThinking(data && data.text ? data.text : '');
        return;
    }
    // Commander 每轮的思考/计划（写在 @@@@ 协议之外）：累积展示为「Agent 计划」块
    if (etype === 'thinking') {
        appendThinking(data && data.text ? data.text : '');
        return;
    }
    // 最终 AI 回复：直接渲染为 AI 消息气泡
    if (etype === 'ai_final_reply') {
        hideTyping();
        endThinking();  // 思考块标记为完成
        endPlan();      // Agent 计划块标记为完成
        if (data && data.text) {
            const cls = data.type === 'error' ? 'error' : 'ai';
            addMsg(data.text, cls);
        }
        setStatus('online');
        sending = false;
        // 完成任务
        const tid = (data && data.task_id) ? String(data.task_id) : null;
        if (tid) completeTask(tid, data.type === 'error' ? 'failed' : 'done');
        return;
    }
    // 任务开始
    if (etype === 'task_started') {
        return;   // 已在函数开头处理（addTask）
    }
    // Agent 向用户提问（对标 OpenCode question）
    if (etype === 'question') {
        const q = (data && data.question) ? data.question : '';
        addMsg('❓ ' + (q || '（Agent 向你提问）'), 'question');
        return;
    }
    // 系统提示（不是错误）：例如「该标签页上有任务正在执行」。
    // 注意：这里【不再】渲染任何「排队」话术 —— 多任务本来就该并行（每个任务/子代理
    // 在同一浏览器里各占一个标签页），旧版那句「上一条任务还在执行，你的新指令已排队」
    // 会让用户误以为软件在无谓地排队，用户已明确要求去掉。现在若真出现提示，
    // 说明是【同一个标签页】被并发驱动，措辞必须点明「该标签页」，而不是笼统的"排队"。
    if (etype === 'warning' || etype === 'info') {
        const w = (data && data.text) ? data.text : '';
        if (w) addMsg('⏳ ' + w, 'system');
        return;
    }
    // 会话列表变更（多会话管理）
    if (etype === 'session_updated') {
        if (data && data.sessions) SESSIONS = data.sessions;
        if (data && data.current != null) CURRENT_SESSION = data.current;
        renderSessionBar();
        return;
    }

    const labels = {
        thinking: '思考中...',
        tool_start: '开始工具',
        tool_end: '工具成功',
        tool_error: '工具失败',
        command_executing: '执行命令...',
        command_success: '命令成功',
        command_detected: '识别指令',
        ai_final_reply: '回复中...',
    };
    const label = labels[etype] || etype;

    // 工具结束：把工具输出渲染成可折叠「结果卡」，diff 自动高亮（对标 opencode）
    if (etype === 'tool_end' && data && data.output) {
        // 【产物归属】把本工具产出的文件登记到当前任务名下，
        // 这样侧边栏按任务过滤时才能取到「这个任务自己的产物」。
        try { recordArtifactsFromTool(data); } catch (e) {}
        renderToolResult(data.tool || label, data.status || 'success', data.output);
        return;
    }

    let extra = '';
    if (data) {
        if (typeof data === 'object') {
            try { extra = JSON.stringify(data).substring(0, 100); } catch {}
        } else {
            extra = String(data).substring(0, 100);
        }
    }
    if (extra) {
        addMsg(`[${label}] ${escapeHtml(String(extra))}`, 'log');
    } else {
        addMsg(`[${label}]`, 'log');
    }
}

// 把工具输出渲染成可折叠结果卡；若内容像统一 diff 则逐行着色
function renderToolResult(tool, status, output) {
    const wrap = document.createElement('div');
    wrap.className = 'tool-result' + (status === 'error' ? ' err' : '');
    const head = document.createElement('div');
    head.className = 'tr-head';
    const arrow = status === 'error' ? '✗' : '✓';
    head.innerHTML = `<span class="tr-dot"></span><span>${arrow} ${escapeHtml(String(tool))}</span>`
        + `<span style="margin-left:auto;opacity:.55">点击${'展开/收起'}</span>`;
    const body = document.createElement('pre');
    body.className = 'tr-body';
    // 先剥掉「写入/删除被拦截」等环境内部噪音再渲染，避免 PPT 清理的 safe-delete 报文刷屏
    body.innerHTML = renderDiffLines(stripNoise(output));
    wrap.appendChild(head);
    wrap.appendChild(body);
    head.onclick = () => wrap.classList.toggle('collapsed');
    // 默认：成功且内容像 diff/较长时收起，纯短文本展开
    if (status !== 'error' && output.length > 400) wrap.classList.add('collapsed');
    addMsgNode(wrap);
}

function renderDiffLines(text) {
    // 统一 diff 着色：+ 行绿、- 行红、@@ hunk 蓝、其余普通
    const looksDiff = /(^|\n)([-+]\s|\+\+\+ |--- |@@ )/.test(text);
    if (!looksDiff) return escapeHtml(text);
    const lines = String(text).split('\n');
    let html = '';
    for (const ln of lines) {
        if (ln.startsWith('+++ ') || ln.startsWith('--- ')) {
            html += `<span class="diff-hunk">${escapeHtml(ln)}</span>`;
        } else if (ln.startsWith('@@')) {
            html += `<span class="diff-hunk">${escapeHtml(ln)}</span>`;
        } else if (ln.startsWith('+')) {
            html += `<span class="diff-add">${escapeHtml(ln)}</span>`;
        } else if (ln.startsWith('-')) {
            html += `<span class="diff-del">${escapeHtml(ln)}</span>`;
        } else if (ln.startsWith(' ')) {
            html += `<span class="diff-ctx">${escapeHtml(ln)}</span>`;
        } else {
            html += escapeHtml(ln) + '\n';
        }
    }
    return html;
}

// ── 子代理事件聚合渲染：把带 subagent_task_id 标记的事件，聚合进一张可折叠卡 ──
const SUBAGENT_CARDS = {};  // task_id -> {head, body, status, lastType}
function upsertSubagentCard(data, etype) {
    const tid = data.subagent_task_id;
    if (!tid) return;
    let card = SUBAGENT_CARDS[tid];
    if (!card) {
        const wrap = document.createElement('div');
        wrap.className = 'subagent-block';
        const head = document.createElement('div');
        head.className = 'sa-head';
        head.innerHTML = `<span class="sa-dot"></span><span>🤖 子代理</span>`
            + `<span class="sa-q">${escapeHtml(data.subagent_query || '')}</span>`
            + `<span class="sa-status">运行中</span>`;
        const body = document.createElement('div');
        body.className = 'sa-body';
        wrap.appendChild(head); wrap.appendChild(body);
        head.onclick = () => wrap.classList.toggle('collapsed');
        addMsgNode(wrap);
        card = { wrap, head, body, statusEl: head.querySelector('.sa-status'),
                 qEl: head.querySelector('.sa-q'), seen: 0 };
        SUBAGENT_CARDS[tid] = card;
    }
    const et = data.subagent_type || etype || '';
    const text = (typeof data === 'object' && data.text) ? data.text : (data.output || '');
    // 工具调用一行：🔧 工具名 → 结果摘要；思考一行：💭 摘要
    let line, cls = '';
    if (et === 'tool_end') { line = '🔧 ' + (data.tool || '') + ' ✓'; cls = 'ok'; }
    else if (et === 'tool_start') { line = '🔧 ' + (data.tool || '') + ' …'; }
    else if (et === 'tool_error' || et === 'command_error') { line = '✗ ' + (data.tool || et) + ' ' + String(text).slice(0,120); cls = 'err'; }
    else if (et === 'thinking') { line = '💭 ' + String(text).slice(0, 120); }
    else { line = et + (text ? ' ' + String(text).slice(0,120) : ''); }
    const div = document.createElement('div');
    div.className = 'sa-line' + (cls ? ' ' + cls : '');
    div.textContent = line;
    card.body.appendChild(div);
    card.body.scrollTop = card.body.scrollHeight;
    card.seen++;
    // 收尾：done / 成功标记 → 卡头变绿"已完成"
    if (data.subagent_done || et === 'done') {
        card.wrap.classList.add('done');
        card.statusEl.textContent = '已完成';
        card.statusEl.classList.add('done');
    } else if (et === 'tool_error' || et === 'command_error') {
        card.statusEl.textContent = '出错';
    }
}

// 把任意 DOM 节点作为一条消息追加（避免 addMsg 的 innerHTML 转义处理）
function addMsgNode(node) {
    const box = document.getElementById('messages');
    if (!box) { document.body.appendChild(node); return; }
    box.appendChild(node);
    box.scrollTop = box.scrollHeight;
}


async function sendMsg(msg) {
    inputEl.value = msg;
    await sendInput();
}

// ── GUI 本地按钮：状态 / 帮助 ──
// 【重要】这些是软件自身的功能，必须点击**瞬间**由本地渲染出来。
// 旧实现走 sendCmd('status') → 经过 /command → 变成一条消息发给 AI，
// 既慢（要等后端和模型）又出现在对话区，属于明确禁止的行为。
// 现在改为：只打本地 /status、/help 端点，直接把自己渲染成一个本地卡片，
// 绝不进发送队列、绝不发给模型。
async function showLocalPanel(kind) {
    try {
        const r = await fetch(API + '/' + kind, { signal: AbortSignal.timeout(4000) });
        const d = await r.json();
        addLocalCard((d && d.text) ? d.text : '（无内容）');
    } catch (e) {
        addLocalCard('⚠️ 本地' + kind + '读取失败：' + e.message);
    }
}

// 把本地信息渲染成一张「软件自己的」卡片 —— 不进消息流、不参与发送。
function addLocalCard(text) {
    const box = document.getElementById('messages');
    if (!box) return;
    const node = document.createElement('div');
    node.className = 'msg msg-system local-card';
    node.innerHTML = '<div class="local-card-body"></div>';
    node.querySelector('.local-card-body').textContent = text;
    box.appendChild(node);
    box.scrollTop = box.scrollHeight;
    // 本地卡片是瞬时的软件反馈，3 秒后自动淡出，不污染对话记录。
    setTimeout(() => { try { node.remove(); } catch (e) {} }, 8000);
}

// sendCmd 已移除：它会把命令当普通消息经 sendInput 发给 AI。
// 所有 GUI 自己的操作（状态/帮助/会话管理/新建对话）都改为本地端点，
// 点击即时响应、不进对话、不发给模型。

// ── 新建对话（真正的独立对话）──
// 旧实现点「➕ 新建」只是 sendCmd('新对话')，把这三个字当成普通消息发给 AI，
// 既不清空界面、后端也多半不认 → 用户感觉「完全没反应 / 不能新建对话」。
// 这里改成：调后端命令「新对话」（session.start_new_conversation，会清上下文
// 并在平台点「新对话」开独立页），成功后把整个界面重置成干净的空对话。
async function newConversation() {
    if (switching) return; // 正在切平台时别动
    if (NEWING) return;    // 防连点
    NEWING = true;
    // 回看历史时先回实时视图
    if (HIST_VIEW) closeHistoryView();
    addMsg('🗂 正在开一个全新的独立对话（会在平台网页真正新建一个对话页，请稍候）...', 'system');
    try {
        // 【关键】走同步端点 /new_conversation：后端内部真实调用
        // start_new_conversation（含 navigate_to_chat，即在平台网页开新对话页），
        // 做完才回包。所以这里拿到 ok 就是真的成功了，不用盲猜、不用定时刷新。
        // 不再走 /command（那是异步的，只会回 accepted，界面无法确定结果）。
        const r = await fetch(API + '/new_conversation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(120000),
        });
        const d = await safeJson(r);
        const ok = (d && (d.type === 'ok' || d.type === 'accepted'));
        // 无论成功与否都先重置界面视图（清消息 + 清产物绑定 + 解锁发送）
        resetChatView();
        if (ok) {
            addMsg('✅ 已开启一个全新的独立对话（独立上下文）。之前的对话已存进「📚 历史记录」。', 'system');
        } else {
            addMsg('⚠️ 新建对话未完成：' + ((d && d.text) ? d.text : '后端未确认'), 'error');
        }
        setTimeout(() => { try { loadHistory(); } catch (e) {} }, 800);
    } catch (e) {
        addMsg('新建对话失败: ' + e.message, 'error');
    } finally {
        NEWING = false;
    }
}

// ── 切回旧任务并继续聊（把某条历史任务的上下文还原成当前活动会话，接着发新消息）──
// 后端 /command 原生支持「恢复对话 <id>」（restore_conversation：把该任务的会话 JSON
// 载入为当前会话 + 导航回平台对应网页），这里做三件事：
//   1) 调后端恢复该任务；
//   2) 退出历史只读回看态（HIST_VIEW 清空），让后续发送走「实时活动会话」；
//   3) 界面切回实时视图并提示「已恢复」，用户直接在输入框发新话即可继续该任务。
async function resumeTask(taskId, fallbackTitle) {
    if (!taskId || switching) return;
    if (HIST_VIEW) closeHistoryView();          // 回实时视图，避免只读态拦发送
    addMsg(`🔄 正在切回任务「${fallbackTitle || taskId}」并恢复其上下文...`, 'system');
    try {
        const r = await fetch(API + '/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: '恢复对话 ' + taskId }),
            signal: AbortSignal.timeout(60000),
        });
        const d = await safeJson(r);
        const ok = d && (d.type === 'accepted' || d.type === 'ok');
        // 恢复结果会走 SSE（ai_final_reply）。这里只重置界面、把发送解锁，让用户接着聊。
        sending = false;
        if (typeof hideTyping === 'function') hideTyping();
        sendBtn.disabled = false;
        if (ok) {
            addMsg('✅ 已切回任务「' + (fallbackTitle || taskId) + '」，其上下文已还原为当前会话。\n'
                 + '直接在下面输入框发新消息即可继续这个任务；点「➕ 新建对话」可随时开全新上下文。', 'system');
        } else {
            addMsg('⚠️ 恢复任务：' + ((d && d.text) ? d.text : '后端未确认') + '\n可点「📚 历史记录」回看该任务内容。', 'system');
        }
        // 刷新历史抽屉（当前任务标记）
        setTimeout(() => { try { loadHistory(); } catch (e) {} }, 1200);
    } catch (e) {
        addMsg('切回任务失败: ' + e.message, 'error');
    }
}

// 把界面重置成「干净的空对话」：清掉消息区/本轮任务条/子代理卡/发送状态
function resetChatView() {
    const msgDiv = document.getElementById('messages');
    // 【2026-09-21 修复】重置界面时把「回看历史」的快照缓存一并丢弃。
    // 否则缓存里那份旧 HTML 会在后续 closeHistoryView 时盖回来，
    // 把刚清空的实时视图又填成旧内容（用户看到「发了消息气泡却不见了」）。
    LIVE_DOM_CACHE = null;
    HIST_VIEW = null;
    if (msgDiv) {
        msgDiv.innerHTML = '<div class="msg msg-system">已开启全新对话</div>'
            + '<div class="welcome"><div class="icon">🌵</div>'
            + '<h1>仙人掌 Agent · 新对话</h1>'
            + '<p>发一条任务（或点「📚 历史记录」回看旧任务）。上一段对话已自动保存为独立任务。</p></div>';
    }
    // 任务条只保留「仍在运行」的（已完成的旧任务清出当前界面，仍在历史记录里可回看）
    TASKS = TASKS.filter(t => t.status === 'running');
    if (typeof renderTaskList === 'function') renderTaskList();
    // 清子代理卡
    try { for (const k in SUBAGENT_CARDS) delete SUBAGENT_CARDS[k]; } catch (e) {}
    // 释放发送锁
    sending = false;
    if (typeof hideTyping === 'function') hideTyping();
    if (typeof setStatus === 'function' && !switching) setStatus('online');
    CURRENT_TASK_ID = null;
    LAST_USER_TEXT = '';
    // 【产物上下文修复】全新对话 = 还没有任何任务 → 右侧产物栏必须清空。
    // 旧实现漏了这一步，导致「一个任务都没建，旁边却堆着一堆任务产物」。
    activeTaskId = null;
    if (typeof bindFileScope === 'function') bindFileScope(null);
    if (typeof clearPendingAttachments === 'function') clearPendingAttachments();
}

// ── 历史对话抽屉 ──
async function toggleHistory(show) {
    const drawer = document.getElementById('historyDrawer');
    const backdrop = document.getElementById('historyBackdrop');
    const wantShow = (show === undefined) ? !drawer.classList.contains('show') : show;
    if (wantShow) {
        drawer.classList.add('show');
        backdrop.classList.add('show');
        await loadHistory();
    } else {
        drawer.classList.remove('show');
        backdrop.classList.remove('show');
    }
}

async function loadHistory() {
    const list = document.getElementById('historyList');
    list.innerHTML = '<div class="history-empty">加载中...</div>';
    try {
        const r = await fetch(API + '/conversations', { signal: AbortSignal.timeout(5000) });
        const data = await safeJson(r);
        const tasks = (data && data.tasks) || [];
        if (!tasks.length) {
            list.innerHTML = '<div class="history-empty">暂无历史对话<br>发一条消息或点「➕ 新建」即可创建</div>';
            return;
        }
        list.innerHTML = '';
        for (const t of tasks) {
            const item = document.createElement('div');
            item.className = 'history-item';
            const plat = (t.platform || '?').toUpperCase();
            const time = (t.updated_at || '').replace('T', ' ').slice(0, 19);
            const title = (t.title || '(无标题)').replace(/</g, '&lt;');
            const safeId = String(t.id).replace(/'/g, '');
            item.innerHTML =
                `<div class="hi-title">${title}</div>` +
                `<div class="hi-meta"><span class="hi-plat">${plat}</span>` +
                `<span>${time}</span></div>` +
                `<div class="hi-actions">` +
                `<span class="hi-resume" onclick="event.stopPropagation(); resumeTask('${safeId}','${safeId}')">▶ 恢复</span>` +
                `<span class="hi-delete" onclick="event.stopPropagation(); deleteTask('${safeId}')" title="删除">✕</span>` +
                `</div>`;
            item.onclick = () => {
                toggleHistory(false);
                openHistoryTask(t.id, t.file, t.title, t.platform, time);
            };
            list.appendChild(item);
        }
    } catch (e) {
        list.innerHTML = '<div class="history-empty">加载失败：' + e.message + '</div>';
    }
}

async function deleteTask(taskId) {
    if (!confirm('确定要删除这条对话记录吗？')) return;
    try {
        const resp = await fetch(API + '/conversations/' + taskId, { method: 'DELETE' });
        const data = await safeJson(resp);
        if (data.type === 'ok') {
            addMsg('已删除对话', 'system');
            loadHistory();
        } else {
            addMsg('删除失败: ' + (data.text || '未知错误'), 'error');
        }
    } catch (e) {
        addMsg('删除失败: ' + e.message, 'error');
    }
}

async function deleteFile(filename) {
    if (!confirm('确定要删除这个文件吗？')) return;
    try {
        const resp = await fetch(API + '/files/' + encodeURIComponent(filename), { method: 'DELETE' });
        const data = await safeJson(resp);
        if (data.type === 'ok') {
            renderFileSidebar();
            renderAttachments();
        } else {
            addMsg('删除失败: ' + (data.text || '未知错误'), 'error');
        }
    } catch (e) {
        addMsg('删除失败: ' + e.message, 'error');
    }
}

let switching = false;
async function safeJson(resp) {
    // 服务端若返回非 JSON（如 500 错误页），兜底返回可读的错误体，而不是抛「json 报错」
    try {
        return await resp.json();
    } catch (e) {
        let raw = '';
        try { raw = await resp.text(); } catch {}
        return { type: 'error', text: '服务端返回了非 JSON 内容：\n' + String(raw || resp.status).slice(0, 500) };
    }
}
async function switchPlatform(platform) {
    if (switching) return;
    switching = true;

    const pname = (PLATFORMS[platform] && PLATFORMS[platform].display) || platform;

    // 高亮目标平台，禁用所有平台按钮避免重复点击
    const platBtns = document.querySelectorAll('.sidebar button[id^=pl-]');
    platBtns.forEach(b => { b.classList.remove('active'); b.disabled = true; });
    const el = document.getElementById('pl-' + platform);
    if (el) el.classList.add('active');

    addMsg(`正在切换到 ${pname}（首次切换需启动浏览器，请稍候，最多约2分钟）...`, 'system');
    setStatus('busy');

    try {
        const resp = await fetch(API + '/platform', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platform: platform }),
            signal: AbortSignal.timeout(125000),
        });
        const data = await safeJson(resp);
        addMsg(data.text || '', data.type === 'error' ? 'error' : 'system');
        // 更新当前平台配置并重新渲染控件
        CURRENT_PLATFORM = platform;
        CURRENT_PLATFORM_CFG = PLATFORMS[platform] || null;
        renderModelSelect(CURRENT_PLATFORM_CFG);
        renderThinkingControl(CURRENT_PLATFORM_CFG);
        renderSearchControl(CURRENT_PLATFORM_CFG);
        setStatus('online');
    } catch(e) {
        addMsg('切换平台失败: ' + e.message, 'error');
        setStatus('online');
    } finally {
        platBtns.forEach(b => { b.disabled = false; });
        switching = false;
    }
}

// ── 主题切换 ──
function toggleTheme() {
    document.documentElement.classList.toggle('light');
    const isLight = document.documentElement.classList.contains('light');
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = isLight ? '☀️ 浅色' : '🌙 深色';
    // 通知 Qt 窗口同步背景（仅在 desktop 模式下有效）
    if (window.QtBridge) {
        window.QtBridge.setTheme(isLight ? 'light' : 'dark');
    }
}

// ── 附件：拖放 + 文件选择 ──
function renderAttachments() {
    const list = document.getElementById('attachList');
    list.innerHTML = '';
    if (!pendingAttachments.length) { list.style.display = 'none'; return; }
    list.style.display = 'flex';
    pendingAttachments.forEach((p, i) => {
        const name = p.split(/[\\/]/).pop();
        const chip = document.createElement('div');
        chip.className = 'attach-chip';
        chip.innerHTML = `<span class="att-name">📎 ${escapeHtml(name)}</span>` +
            `<span class="att-x" onclick="removeAttachment(${i})">✕</span>`;
        list.appendChild(chip);
    });
    renderFileSidebar(); // 同时更新侧边栏
}
function addAttachment(path) {
    if (!pendingAttachments.includes(path)) pendingAttachments.push(path);
    renderAttachments();
}
function removeAttachment(i) {
    pendingAttachments.splice(i, 1);
    renderAttachments();
}
// 清空「待发送附件」暂存区（新建对话时调用，避免旧任务的待传附件串场）
function clearPendingAttachments() {
    pendingAttachments = [];
    renderAttachments();
}

// ── 产物归属：把工具产出的文件登记到「当前任务」名下 ──
// 侧边栏按任务过滤，靠的就是这份登记；没有当前任务时不登记（产物不落全局桶）。
function recordArtifactsFromTool(data) {
    const cid = activeTaskId || CURRENT_TASK_ID;
    if (!cid) return;                       // 没有任务 → 不登记
    const txt = String((data && data.output) || '');
    if (!txt) return;
    const ART = /\.(docx?|pptx?|xlsx?|csv|pdf|txt|md|html?|json|py|png|jpe?g|gif|svg)\b/gi;
    const found = [];
    // 工具输出里常见的「已写入 / 保存到 / 路径:」后面跟的路径
    const pathRe = /([A-Za-z]:[\\\/][^\s"'<>|]+?\.[A-Za-z0-9]{1,5})/g;
    let m;
    while ((m = pathRe.exec(txt)) !== null) {
        if (ART.test(m[1])) { found.push(m[1]); ART.lastIndex = 0; }
    }
    if (!found.length) return;
    try {
        fetch(API + '/register_artifacts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: cid, paths: found }),
        }).then(() => {
            // 若用户此刻正看着这个任务，刷新一下侧边栏
            if (String(FILE_SCOPE_CONV) === String(cid)) loadAttachments(cid);
        }).catch(() => {});
    } catch (e) {}
}

// ── 右侧文件预览侧边栏 ──
let FILE_LIST = []; // 当前会话的附件列表

// 当前挂载在侧边栏的会话 id：null / '' 表示「当前没有任务」。
// 任务产物必须与对话记录一样是任务私有的 —— 没选任务就不能显示任何产物。
let FILE_SCOPE_CONV = null;

async function loadAttachments(convId) {
    // 显式传入会话 id；不传则沿用当前挂载的会话；都没有 → 清空列表
    const cid = (convId === undefined) ? FILE_SCOPE_CONV : convId;
    if (!cid) {
        FILE_SCOPE_CONV = null;
        FILE_LIST = [];
        renderFileSidebar();
        return;
    }
    FILE_SCOPE_CONV = cid;
    try {
        const r = await fetch(API + '/attachments?conversation_id=' + encodeURIComponent(cid),
            { signal: AbortSignal.timeout(5000) });
        const data = await safeJson(r);
        // 竞态保护：请求返回时若已切到别的任务，丢弃这次结果
        if (FILE_SCOPE_CONV !== cid) return;
        FILE_LIST = (data && data.files) ? data.files : [];
        renderFileSidebar();
    } catch (e) {
        if (FILE_SCOPE_CONV === cid) {
            FILE_LIST = [];
            renderFileSidebar();
        }
    }
}

// 传入任务 id（= 会话 id）时精确绑定；传 null 清空侧边栏
function bindFileScope(taskId) {
    loadAttachments(taskId || null);
}

function renderFileSidebar() {
    const container = document.getElementById('fileList');
    if (!container) return;
    
    const allFiles = [...new Set([...FILE_LIST, ...pendingAttachments.map(p => ({ path: p, name: p.split(/[\\/]/).pop(), size: 0 }))])];
    
    if (!allFiles.length) {
        container.innerHTML = '<div class="file-empty">暂无文件<br>上传文件或拖放文件到此处</div>';
        return;
    }
    
    container.innerHTML = '';
    allFiles.forEach((f, i) => {
        const ext = f.name.split('.').pop().toLowerCase();
        let icon = '📄';
        if (['jpg', 'jpeg', 'png', 'gif', 'bmp'].includes(ext)) icon = '🖼️';
        else if (ext === 'pdf') icon = '📕';
        else if (ext === 'pptx' || ext === 'ppt') icon = '📊';
        else if (ext === 'docx' || ext === 'doc') icon = '📘';
        else if (ext === 'xlsx' || ext === 'xls') icon = '📗';
        
        const item = document.createElement('div');
        item.className = 'file-item';
        item.onclick = () => previewFile(f.path);
        item.innerHTML = `
            <div class="file-icon">${icon}</div>
            <div class="file-info">
                <div class="file-name">${escapeHtml(f.name)}</div>
                <div class="file-size">${formatSize(f.size)}</div>
            </div>
            <div class="file-close" onclick="event.stopPropagation(); deleteFile('${escapeHtml(f.name)}')" title="删除">✕</div>
        `;
        container.appendChild(item);
    });
}

function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function previewFile(path) {
    const container = document.getElementById('filePreviewContainer');
    if (!container) return;

    try {
        const r = await fetch(API + '/preview?path=' + encodeURIComponent(path), {
            signal: AbortSignal.timeout(10000)
        });
        const data = await safeJson(r);

        if (!data || !data.html) {
            container.innerHTML = '<div class="file-preview"><pre>无法预览此文件类型</pre></div>';
            return;
        }

        // 可编辑文本文件
        if (data.editable && data.raw !== undefined) {
            const isImageOrOther = !data.raw;
            container.innerHTML = `
                <div class="file-preview-edit">
                    <div class="edit-toolbar">
                        <span class="file-name">📝 ${escapeHtml(data.path ? data.path.split(/[\\/]/).pop() : '文件')}</span>
                        <button class="btn" style="padding:4px 10px;font-size:11px;" onclick="toggleEditView(this)">👁️ 预览</button>
                        <button class="btn btn-primary" style="padding:4px 10px;font-size:11px;" onclick="saveFile('${escapeHtml(path)}', this)">💾 保存</button>
                    </div>
                    <textarea id="editTextarea">${escapeHtml(data.raw)}</textarea>
                </div>`;
            return;
        }

        // 普通预览（图片/PDF/其他）
        container.innerHTML = `<div class="file-preview">${data.html}</div>`;
    } catch (e) {
        container.innerHTML = '<div class="file-preview"><pre>加载失败: ' + escapeHtml(e.message) + '</pre></div>';
    }
}

async function saveFile(path, btn) {
    const textarea = document.getElementById('editTextarea');
    if (!textarea) return;
    btn.disabled = true;
    btn.textContent = '⏳ 保存中...';
    try {
        const r = await fetch(API + '/save?path=' + encodeURIComponent(path), {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            body: textarea.value,
            signal: AbortSignal.timeout(15000)
        });
        const data = await safeJson(r);
        if (data.type === 'ok') {
            btn.textContent = '✅ 已保存';
            setTimeout(() => { btn.disabled = false; btn.textContent = '💾 保存'; }, 2000);
        } else {
            btn.disabled = false;
            btn.textContent = '💾 保存';
            alert('保存失败: ' + (data.text || '未知错误'));
        }
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '💾 保存';
        alert('保存失败: ' + e.message);
    }
}

function toggleEditView(btn) {
    const ta = document.getElementById('editTextarea');
    if (!ta) return;
    const isEditing = ta.style.display !== 'none';
    // 切换为预览模式：隐藏 textarea，显示预格式化的原始内容
    if (!isEditing) {
        ta.style.display = 'none';
        const pre = document.createElement('pre');
        pre.style.cssText = 'font-size:11px;white-space:pre-wrap;word-break:break-word;color:var(--text2);font-family:ui-monospace,Menlo,Consolas,monospace;max-height:400px;overflow:auto;margin:0;';
        pre.textContent = ta.value;
        ta.parentNode.insertBefore(pre, ta.nextSibling);
        btn.textContent = '✏️ 编辑';
        btn.onclick = () => {
            pre.remove();
            ta.style.display = '';
            btn.textContent = '👁️ 预览';
            btn.onclick = () => toggleEditView(btn);
        };
    }
}

// ── 多会话管理（对标 OpenCode 会话/任务并行） ──
function renderSessionBar() {
    const box = document.getElementById('sessionList');
    if (!box) return;
    box.innerHTML = '';
    if (!SESSIONS.length) {
        box.innerHTML = '<div style="color:var(--text2);font-size:11px;padding:2px 4px;">（暂无并行会话）</div>';
        return;
    }
    SESSIONS.forEach(s => {
        const el = document.createElement('div');
        el.className = 'session-item' + (s.id === CURRENT_SESSION ? ' active' : '');
        el.onclick = () => switchSession(s.id);
        el.innerHTML = `<span class="si-dot">${s.id === CURRENT_SESSION ? '●' : '○'}</span>` +
            `<span class="si-name">${escapeHtml(s.name || ('会话 ' + s.id))}</span>` +
            `<span class="si-delete" onclick="event.stopPropagation(); deleteSession(${s.id})" title="删除会话">×</span>`;
        box.appendChild(el);
    });
}
// 会话管理：走本地 /session_cmd，由软件自己即时完成。
// 【重要】旧实现把这串 '@@@@{"tool":"session_manager",...}@@@@' 当 command 发给
// /command，导致内部协议被当成一条消息投给模型、还显示在对话区。GUI 的点击必须
// 本地即时响应，不能变成发给 AI 的指令。
async function sessionCmd(action, id) {
    const r = await fetch(API + '/session_cmd', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ action: action, id: id }),
        signal: AbortSignal.timeout(20000),
    });
    return await safeJson(r);
}
async function createSession() {
    try {
        const d = await sessionCmd('create');
        addLocalCard('🗂 ' + ((d && d.text) ? d.text : '已新建会话'));
        try { loadSessions(); } catch (e) {}
    } catch (e) { addLocalCard('新建会话失败: ' + e.message); }
}
async function switchSession(id) {
    try {
        const d = await sessionCmd('switch', id);
        addLocalCard('🗂 ' + ((d && d.text) ? d.text : ('已切换到会话 ' + id)));
        CURRENT_SESSION = id;
        try { loadSessions(); } catch (e) {}
    } catch (e) { addLocalCard('切换会话失败: ' + e.message); }
}
async function deleteSession(id) {
    if (!confirm('确定删除这个会话吗？')) return;
    try {
        const d = await sessionCmd('delete', id);
        addLocalCard('🗂 ' + ((d && d.text) ? d.text : ('已删除会话 ' + id)));
        loadSessions();
    } catch (e) { addLocalCard('删除会话失败: ' + e.message); }
}
async function uploadFile(file) {
    try {
        const fd = new FormData();
        fd.append('file', file, file.name);
        const r = await fetch(API + '/upload', { method: 'POST', body: fd, signal: AbortSignal.timeout(60000) });
        const data = await safeJson(r);
        if (data && data.files && data.files.length) {
            for (const f of data.files) addAttachment(f.path);
        } else if (data && data.type === 'error') {
            addMsg('附件上传失败: ' + data.text, 'error');
        }
    } catch (e) {
        addMsg('附件上传失败: ' + e.message, 'error');
    }
}
// 原生文件选择（像正常软件一样弹系统对话框）
function openFilePicker() {
    const inp = document.getElementById('fileInput');
    if (inp) inp.click();
}
function onFilePicked(e) {
    const files = e.target.files;
    if (files && files.length) {
        for (const f of files) uploadFile(f);
    }
    e.target.value = '';  // 允许重复选择同一文件
}
// 拖放：全局拦截，确保文件不会被浏览器直接打开，并触发上传
(function setupDrop() {
    const zone = document.getElementById('inputArea');
    let depth = 0;
    const hasFiles = (e) => e.dataTransfer && e.dataTransfer.types &&
        (Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0 ||
         Array.prototype.indexOf.call(e.dataTransfer.types, 'application/x-moz-file') >= 0);
    window.addEventListener('dragover', (e) => { e.preventDefault(); });
    window.addEventListener('dragenter', (e) => {
        e.preventDefault();
        if (hasFiles(e)) { depth++; zone.classList.add('drag-over'); }
    });
    window.addEventListener('dragleave', (e) => {
        e.preventDefault();
        depth = Math.max(0, depth - 1);
        if (depth === 0) zone.classList.remove('drag-over');
    });
    window.addEventListener('drop', (e) => {
        e.preventDefault();
        depth = 0; zone.classList.remove('drag-over');
        const files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) {
            for (const f of files) uploadFile(f);
        }
    });
})();

async function sendInput(forceText) {
    const text = (forceText || inputEl.value).trim();
    if (!text) return;
    dismissOnboarding();  // 用户一旦动手发消息，引导卡就该让位

    // 正在回看历史任务时，一旦要发消息就自动回到实时视图
    if (HIST_VIEW) closeHistoryView();

    // 【修复1】上一条还没回来时，明确告知用户，绝不静默丢弃输入
    // （旧代码是 `if (!text || sending) return;`，后端卡住时会把消息无声吃掉）
    if (sending) {
        const sec = sendStartTime ? Math.round((Date.now() - sendStartTime) / 1000) : 0;
        addMsg('⏳ 上一条消息仍在处理中（已 ' + sec + ' 秒），暂时无法发送新的。\n'
             + '若长时间无响应，说明后端卡住或已退出 —— 请检查运行 terminal.py 的终端窗口。\n'
             + '下面这条【尚未发送】，请稍后重发：\n' + text.slice(0, 120), 'error');
        return;
    }

    // 【修复2】发送前快速探活：后端不在就别清空输入框、别假装已发送。
    // 注意：后端启动期间 HTTP 服务会先起来、但 Agent 尚未就绪（浏览器初始化
    // 约需 1-2 分钟），此时 /health 仍返回 200，必须检查 agent_ready 字段，
    // 否则消息会被发给「还没醒」的 Agent 然后石沉大海。
    try {
        const h = await fetch(API + '/health', { signal: AbortSignal.timeout(2500) });
        if (!h.ok) throw new Error('HTTP ' + h.status);
        const hj = await h.json();
        if (!hj.agent_ready) {
            addMsg('⏳ Agent 仍在启动中（浏览器初始化较慢，通常需 1~2 分钟）。\n'
                 + '请等左下角状态变为「已连接」后再发送。\n'
                 + '你的输入已保留在输入框，不会丢失。', 'error');
            setStatus('offline');
            return;
        }
    } catch (e) {
        addMsg('⚠️ 连不上后端（' + API + '），消息【未发送】。\n'
             + '后端未运行或已退出，请先运行 terminal.py 再重试。\n'
             + '（你的输入已保留在输入框，不会丢失）', 'error');
        setStatus('offline');
        return;
    }

    if (!forceText) inputEl.value = '';
    // 【核心修复】自己的消息绝不能被「吃掉」：一发出就立刻在对话区显示用户气泡。
    // 旧实现只靠后端 SSE 回包才显示，任务在后台跑时用户那条话在界面上凭空消失。
    addMsg(text, 'user');
    LAST_USER_TEXT = text;      // 归属到即将开始的这个任务（task_started 到达时归档）
    CURRENT_TASK_ID = null;
    if (pendingAttachments.length) {
        addMsg('📎 附件 ' + pendingAttachments.length + ' 个：' + pendingAttachments.map(p => p.split(/[\\/]/).pop()).join('、'), 'log');
    }
    showTyping();
    sending = true;
    sendStartTime = Date.now();
    sendBtn.disabled = true;
    setStatus('busy');

    try {
        const resp = await fetch(API + '/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: text, attachments: pendingAttachments }),
            signal: AbortSignal.timeout(300000),
        });
        hideTyping();
        const data = await safeJson(resp);
        const rtype = data.type || 'ai';
        const rtext = data.text || '';

        if (rtype === 'accepted') {
            setStatus('busy');
        } else {
            const cls = { ai: 'ai', system: 'system', error: 'error' }[rtype] || 'system';
            addMsg(rtext, cls);
            setStatus('online');
        }
    } catch (e) {
        hideTyping();
        // 【修复3】失败/超时时把用户输入还回输入框，避免任务被"吃掉"
        if (!forceText && !inputEl.value.trim()) inputEl.value = text;
        addMsg('发送失败: ' + e.message + '\n（你的输入已放回输入框，可直接重发）\n'
             + '若持续失败，请确认 terminal.py 正在运行且未卡死。', 'error');
        setStatus('offline');
    }
    if (!forceText) { pendingAttachments = []; renderAttachments(); }
    sending = false;
}

// 当前激活的平台 key
let CURRENT_PLATFORM = 'deepseek';
// 当前平台配置（含 thinking_mode / models）
let CURRENT_PLATFORM_CFG = null;

// 模型选择回调
function onModelChange(model) {
    console.log(`[GUI] 切换到模型: ${model}`);
    const statusEl = document.getElementById('modelStatus');
    if (statusEl && model) {
        const opt = document.getElementById('modelSelect').querySelector(`option[value="${model}"]`);
        const label = opt ? opt.textContent : model;
        statusEl.textContent = `● ${label}`;
    }
    // 同步到后端
    fetch(API + '/model', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({model: model}),
        signal: AbortSignal.timeout(5000)
    }).catch(e => console.error('[GUI] 模型切换失败:', e));
}


// ── 思考模式控件（已取消 AI 代劳）──
// 以前这两个函数把「深度思考 开启/关闭」当自然语言发给 AI，让 AI 自己去网页上
// 点按钮 —— 实测四个平台从未成功过，还把一句话变成一条"任务"污染历史。
// 用户已决定取消该做法：深度思考不再由 AI 代开代关，这里也不再渲染入口。
// （保留空函数是为了防止残留的 onclick 引用报错）
function toggleThinking() {
    if (typeof addMsg === 'function') {
        addMsg('🧠 深度思考已改为界面直控：本版本不再让 AI 代开代关。'
             + '如需切换，请直接在网页平台上切换，或等待后续版本提供直控接口。', 'system');
    }
}
function setThinkingMode(val) { toggleThinking(); }

// 渲染思考控件（已取消：不再渲染 AI 代开关的入口）
function renderThinkingControl(cfg) {
    const container = document.getElementById('thinkingControl');
    if (!container) return;
    container.innerHTML = '';
    if (!cfg) return;
    return;
}

// ── 联网搜索控件 ──
// 只有平台网页真的带该开关（后端 /platforms 返回 search 选择器）才显示。
// 注意：仍遵循「不让 AI 代开代关」的原则 —— 这里只做 UI 展示与状态提示，
// 真正的切换走用户自己操作或在网页上点，避免出现「假装已开启」的谎报。
function renderSearchControl(cfg) {
    const group = document.getElementById('searchGroup');
    const btn = document.getElementById('searchToggleBtn');
    if (!group || !btn) return;
    const has = !!(cfg && cfg.has_search);
    group.style.display = has ? '' : 'none';
    if (!has) return;
    btn.textContent = '🔍 搜索: 网页可控';
    btn.title = '该平台网页提供「联网搜索」开关，可直接在浏览器窗口里点击切换';
}

// 渲染模型下拉框
function renderModelSelect(plat) {
    const sel = document.getElementById('modelSelect');
    const group = document.getElementById('modelGroup');
    if (!sel) return;
    sel.innerHTML = '';

    // 【真实性修复】平台网页上根本没有模型/模式选择器时（如 DeepSeek 网页版），
    // 不要伪造下拉项 —— 直接隐藏整个「模型」控件，避免用户以为能切。
    // models 为空数组 = 该平台没有模型选择器（区别于 undefined = 未配置）。
    if (plat && Array.isArray(plat.models) && plat.models.length === 0) {
        if (group) group.style.display = 'none';
        return;
    }
    if (group) group.style.display = '';

    if (!plat || !plat.models || !plat.models.length) {
        // 未配置模型列表：用平台显示名占位（保持控件可见）
        const opt = document.createElement('option');
        opt.value = plat && plat.key ? plat.key : '';
        opt.textContent = (plat && (plat.display || plat.name)) || '默认';
        sel.appendChild(opt);
        return;
    }
    plat.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.value;
        opt.textContent = m.label;
        sel.appendChild(opt);
    });
    // 默认选第一个
    if (sel.options.length) sel.selectedIndex = 0;
}

// 平台注册表（从 /platforms 动态加载，支持用户自定义新增网页 AI，无需改代码）
let PLATFORMS = {};
async function loadPlatforms() {
    try {
        const r = await fetch(API + '/platforms', { signal: AbortSignal.timeout(3000) });
        if (!r.ok) return;
        const data = await safeJson(r);
        (data.platforms || []).forEach(p => { PLATFORMS[p.key] = p; });
        const box = document.getElementById('platformList');
        if (!box) return;
        box.innerHTML = '';
        data.platforms.forEach(p => {
            const b = document.createElement('button');
            b.id = 'pl-' + p.key;
            b.onclick = () => switchPlatform(p.key);
            b.textContent = p.icon + ' ' + p.display;
            box.appendChild(b);
        });
        // 默认选中第一个平台
        const first = data.platforms[0];
        if (first) {
            CURRENT_PLATFORM = first.key;
            CURRENT_PLATFORM_CFG = first;
            renderModelSelect(first);
            renderThinkingControl(first);
        renderSearchControl(first);
            const activeBtn = document.getElementById('pl-' + first.key);
            if (activeBtn) activeBtn.classList.add('active');
        }
    } catch {}
}

// 健康检查
setInterval(async () => {
    try {
        const r = await fetch(API + '/health', { signal: AbortSignal.timeout(3000) });
        if (r.ok) {
            let d = null;
            try { d = await r.json(); } catch {}
            if (d && d.platform) {
                document.querySelectorAll('.sidebar button[id^=pl-]').forEach(b => b.classList.remove('active'));
                const cur = document.getElementById('pl-' + d.platform);
                if (cur) cur.classList.add('active');
                // 同步当前平台配置
                if (d.platform !== CURRENT_PLATFORM) {
                    CURRENT_PLATFORM = d.platform;
                    CURRENT_PLATFORM_CFG = PLATFORMS[d.platform] || null;
                    renderModelSelect(CURRENT_PLATFORM_CFG);
                    renderThinkingControl(CURRENT_PLATFORM_CFG);
        renderSearchControl(CURRENT_PLATFORM_CFG);
                }
            }
            setStatus('online');
        }
    } catch { if (!sending) setStatus('offline'); }
}, 5000);

// ── 引导对话（首次连接时给一段新手引导）──
// 用独立的 .onboarding 类（不是 .welcome），这样 SSE 的系统日志不会把它顶掉；
// 只在用户真正发消息（sendInput）或开始任务时移除。
function showOnboarding() {
    const msgDiv = document.getElementById('messages');
    if (!msgDiv) return;
    if (msgDiv.querySelector('.onboarding')) return; // 已经有了就别再插
    // 若当前只剩「连接中」占位 welcome，先替换掉
    const pre = msgDiv.querySelector('.welcome');
    if (pre && pre.innerText && pre.innerText.indexOf('连接中') >= 0) pre.remove();
    const box = document.createElement('div');
    box.className = 'onboarding';
    box.innerHTML =
        '<div class="ob-icon">🌵</div>' +
        '<h1>欢迎使用 仙人掌 Agent</h1>' +
        '<div class="ob-body">' +
        '<b>这是干嘛的：</b>把任务交给它，它会自动在网页平台（DeepSeek/通义/豆包/元宝）里帮你干活、生成文件。<br>' +
        '<b>怎么用：</b>① 在左侧「🌐 平台切换」选一个平台 → ② 在下方输入框写任务（可拖文件进来）→ ③ 按 Enter 发送（Shift+Enter 换行）。<br>' +
        '<b>常用：</b>左侧「📚 历史记录」回看旧任务；「➕ 新建对话」开全新独立对话；右侧「📦 文件产物」看生成出的 PPT/Word/Excel。<br>' +
        '<b>控制：</b>任务运行时会出现 ⏹「中断/暂停」和 💬「插话」按钮，随时可打断或补一句。<br>' +
        '<b>现在试试：</b>在输入框写一条任务，比如「帮我生成一份 5 页的 PPT，主题是…」，然后发出去。' +
        '</div>';
    msgDiv.insertBefore(box, msgDiv.firstChild);
    msgDiv.scrollTop = 0;
}
function dismissOnboarding() {
    const box = document.getElementById('messages');
    if (!box) return;
    const o = box.querySelector('.onboarding');
    if (o) o.remove();
}

// 初始连接
loadTranscripts();   // 载入按任务归档的过程记录（历史任务回看用）
(async () => {
    for (let i = 0; i < 30; i++) {
        try {
            const r = await fetch(API + '/health', { signal: AbortSignal.timeout(2000) });
            if (r.ok) {
                setStatus('online');
                // 【引导对话修复】不再只甩一句「已连接」——
                // 第一次进来给一段新手引导，告诉用户能干嘛、怎么操作。
                showOnboarding();
                await loadPlatforms(); // 动态渲染平台按钮
                connectEvents(); // 启动SSE事件
                // 【产物上下文修复】刚启动时还没有选中任何任务，
                // 所以侧边栏必须是空的 —— 产物只属于具体任务，不搞全局堆叠。
                if (typeof bindFileScope === 'function') bindFileScope(null);
                return;
            }
        } catch {}
        await new Promise(r => setTimeout(r, 1000));
    }
    setStatus('offline');
    addMsg('无法连接到终端，请确认 terminal.py 正在运行', 'error');
})();

inputEl.focus();

// 切换文件预览侧边栏显示/隐藏
function toggleFileSidebar() {
    const sidebar = document.getElementById('fileSidebar');
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
    }
    // 翻转按钮箭头
    const btn = document.getElementById('fileSidebarToggle');
    if (btn) {
        btn.textContent = sidebar && sidebar.classList.contains('collapsed') ? '‹' : '›';
    }
}

// 切换左侧边栏显示/隐藏
function toggleSidebar() {
    const sb = document.getElementById('leftSidebar');
    if (sb) {
        sb.classList.toggle('collapsed');
    }
}
