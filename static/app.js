/**
 * app.js — 粤语实时翻译前端逻辑
 * ================================
 *
 * 功能:
 *   - WebSocket 连接管理
 *   - 录音开始/停止控制
 *   - 翻译结果实时显示（带去重和段标记）
 *   - 状态指示灯、计时器、音频电平表
 *   - 错误提示
 *   - 横竖屏适配
 */

// ============================================================
// 状态
// ============================================================

const state = {
    isRecording: false,
    startTime: null,
    timerInterval: null,
    lastText: '',
    lastSegmentId: 0,
    resultCount: 0,
    levelHistory: [],
};

// ============================================================
// DOM 引用
// ============================================================

const $ = (id) => document.getElementById(id);

const dom = {
    statusDot: $('status-dot'),
    statusText: $('status-text'),
    timer: $('timer-display'),
    levelMeter: $('level-meter'),
    levelBar: $('level-meter').querySelector('.level-bar'),
    resultsContainer: $('results-container'),
    emptyState: $('empty-state'),
    btnRecord: $('btn-record'),
    btnLabel: $('btn-label'),
    btnClear: $('btn-clear'),
};

// ============================================================
// SocketIO 连接
// ============================================================

const socket = io({
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionAttempts: Infinity,
});

socket.on('connect', () => {
    console.log('[WS] 已连接');
});

socket.on('disconnect', (reason) => {
    console.log('[WS] 断开:', reason);
    if (state.isRecording) {
        stopRecording();
    }
    setStatus('offline', '连接断开');
});

socket.on('connect_error', (err) => {
    console.error('[WS] 连接失败:', err.message);
    setStatus('offline', '连接中...');
});

// ============================================================
// WebSocket 事件处理
// ============================================================

socket.on('translation_result', (data) => {
    const { text, segment_id, is_interim } = data;
    addResult(text, segment_id, is_interim);
});

socket.on('status_update', (data) => {
    if (data.is_recording !== undefined) {
        if (data.is_recording) {
            setStatus('online', '录音中');
        } else {
            setStatus('offline', stoppingLabel());
        }
    }

    if (data.level !== undefined) {
        updateLevelMeter(data.level);
    }

    if (data.error) {
        showError(data.error);
    }

    if (data.message === 'connected' && state.isRecording) {
        // 重连后恢复状态
        socket.emit('start_recording');
    }
});

// ============================================================
// 录音控制
// ============================================================

dom.btnRecord.addEventListener('click', () => {
    if (state.isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
});

dom.btnClear.addEventListener('click', () => {
    clearResults();
    socket.emit('clear_results');
});

function startRecording() {
    if (state.isRecording) return;

    state.isRecording = true;
    state.startTime = Date.now();
    state.lastText = '';
    state.lastSegmentId = 0;

    // 清空旧结果
    clearResults();

    // UI
    dom.btnRecord.classList.add('recording');
    dom.btnLabel.textContent = '停止';
    setStatus('online', '录音中');
    dom.levelMeter.classList.add('active');

    // 计时器
    startTimer();

    // 通知服务端
    socket.emit('start_recording');
}

function stopRecording() {
    if (!state.isRecording) return;

    state.isRecording = false;

    // UI
    dom.btnRecord.classList.remove('recording');
    dom.btnLabel.textContent = '开始';
    setStatus('offline', '已停止');
    dom.levelMeter.classList.remove('active');
    dom.levelBar.style.height = '0%';

    // 计时器
    stopTimer();

    // 通知服务端
    socket.emit('stop_recording');

    // 更新最后一条为非临时结果
    markLastResultFinal();
}

// ============================================================
// 状态 UI
// ============================================================

function setStatus(mode, label) {
    dom.statusDot.className = 'dot';
    if (mode === 'online') {
        dom.statusDot.classList.add('dot-online');
    } else {
        dom.statusDot.classList.add('dot-offline');
    }
    dom.statusText.textContent = label;
}

function stoppingLabel() {
    return '已停止';
}

// ============================================================
// 计时器
// ============================================================

function startTimer() {
    stopTimer();
    updateTimerDisplay();
    state.timerInterval = setInterval(updateTimerDisplay, 200);
}

function stopTimer() {
    if (state.timerInterval) {
        clearInterval(state.timerInterval);
        state.timerInterval = null;
    }
}

function updateTimerDisplay() {
    if (!state.startTime) {
        dom.timer.textContent = '00:00';
        return;
    }
    const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
    const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const secs = String(elapsed % 60).padStart(2, '0');
    dom.timer.textContent = `${mins}:${secs}`;
}

// ============================================================
// 电平表
// ============================================================

function updateLevelMeter(dbfs) {
    // dBFS 范围: -60 ~ 0 (越接近0越大声)
    // 映射到 0% ~ 100%
    const normalized = Math.min(100, Math.max(0, (dbfs + 60) / 60 * 100));
    dom.levelBar.style.height = `${normalized}%`;

    // 根据电平改变颜色
    if (normalized > 80) {
        dom.levelBar.style.background = 'linear-gradient(to top, #FF4D4F, #FF6B6B)';
    } else if (normalized > 50) {
        dom.levelBar.style.background = 'linear-gradient(to top, #FAAD14, #FFD666)';
    } else {
        dom.levelBar.style.background = 'linear-gradient(to top, var(--accent-green), var(--accent-blue))';
    }
}

// ============================================================
// 翻译结果显示
// ============================================================

function addResult(text, segmentId, isInterim) {
    // 隐藏空状态
    dom.emptyState.classList.add('hidden');

    // 检查语音段切换
    const segmentChanged = segmentId > state.lastSegmentId;
    if (segmentChanged) {
        state.lastSegmentId = segmentId;
    }

    // 去重：如果新文本和上条结果相似度过高，更新上条而非新增
    if (shouldUpdateLast(text)) {
        updateLastResult(text, isInterim);
    } else {
        // 如果段切换，插入分隔线
        if (segmentChanged && state.resultCount > 0) {
            insertSegmentDivider(segmentId);
        }
        appendNewResult(text, segmentId, isInterim);
        state.resultCount++;
    }

    state.lastText = text;
    scrollToBottom();
}

function shouldUpdateLast(text) {
    if (!state.lastText) return false;

    const prev = state.lastText;
    const curr = text;

    // 如果新文本包含了旧文本（增量更新，滑动窗口添加了新内容）
    if (curr.startsWith(prev) || prev.startsWith(curr)) {
        return true;
    }

    // 计算字符重叠率
    const overlap = countOverlap(prev, curr);
    const maxLen = Math.max(prev.length, curr.length);
    const ratio = overlap / maxLen;

    // 重叠超过 60% 认为是同一句话的更新
    return ratio > 0.6;
}

function countOverlap(a, b) {
    // 最长公共子串（简化版）
    let maxLen = 0;
    const m = a.length, n = b.length;
    // 只检查开头对齐和结尾对齐
    for (let len = Math.min(m, n); len > 0; len--) {
        if (a.slice(0, len) === b.slice(0, len)) {
            maxLen = len;
            break;
        }
    }
    return maxLen;
}

function appendNewResult(text, segmentId, isInterim) {
    const item = createResultElement(text, segmentId, isInterim);
    dom.resultsContainer.appendChild(item);
}

function updateLastResult(text, isInterim) {
    const lastItem = dom.resultsContainer.lastElementChild;

    // 跳过分隔线
    let target = lastItem;
    if (target && target.classList.contains('segment-divider')) {
        target = target.previousElementSibling;
    }

    if (target && target.classList.contains('result-item')) {
        const textEl = target.querySelector('.result-text');
        if (textEl) {
            textEl.textContent = text;
            textEl.className = 'result-text';
            if (isInterim) {
                textEl.classList.add('interim-text');
            }
        }
        target.className = 'result-item';
        if (isInterim) {
            target.classList.add('interim');
        }
        target.classList.add('current');
    }
}

function markLastResultFinal() {
    const items = dom.resultsContainer.children;
    if (items.length === 0) return;

    const last = items[items.length - 1];
    if (last && last.classList.contains('result-item')) {
        last.classList.remove('interim');
        last.classList.remove('current');
        const textEl = last.querySelector('.result-text');
        if (textEl) {
            textEl.classList.remove('interim-text');
        }
    }
}

function createResultElement(text, segmentId, isInterim) {
    const div = document.createElement('div');
    div.className = 'result-item';
    if (isInterim) div.classList.add('interim');
    div.classList.add('current');

    const textEl = document.createElement('div');
    textEl.className = 'result-text';
    if (isInterim) textEl.classList.add('interim-text');
    textEl.textContent = text;

    div.appendChild(textEl);

    // 段标记
    if (segmentId > 0) {
        const meta = document.createElement('div');
        meta.className = 'result-meta';
        meta.innerHTML = `<span class="segment-badge">段 ${segmentId}</span>`;
        div.appendChild(meta);
    }

    return div;
}

function insertSegmentDivider(segmentId) {
    const divider = document.createElement('div');
    divider.className = 'segment-divider';
    divider.textContent = `段 ${segmentId}`;
    dom.resultsContainer.appendChild(divider);
}

// ============================================================
// 辅助
// ============================================================

function scrollToBottom() {
    requestAnimationFrame(() => {
        dom.resultsContainer.scrollTop = dom.resultsContainer.scrollHeight;
    });
}

function clearResults() {
    dom.resultsContainer.innerHTML = '';
    dom.emptyState.classList.remove('hidden');
    state.lastText = '';
    state.lastSegmentId = 0;
    state.resultCount = 0;
    dom.levelBar.style.height = '0%';
}

function showError(message) {
    // 移除旧 toast
    const oldToast = document.querySelector('.error-toast');
    if (oldToast) oldToast.remove();

    const toast = document.createElement('div');
    toast.className = 'error-toast';
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================================
// 网络恢复重连
// ============================================================

document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !socket.connected) {
        socket.connect();
    }
});

// ============================================================
// 键盘快捷键（测试用）
// ============================================================

document.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && e.target === document.body) {
        e.preventDefault();
        dom.btnRecord.click();
    }
    if (e.code === 'Escape') {
        dom.btnClear.click();
    }
});

console.log('📢 粤语实时翻译 已就绪');
