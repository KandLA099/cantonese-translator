/**
 * app.js — 粤语实时翻译前端逻辑
 * 纯原生 JS，不依赖任何框架/CDN
 */

const API_BASE = ''; // 同域，无需前缀

// ── DOM 引用 ─────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = {
  statusDot: $('statusDot'),
  timer: $('timer'),
  audioFill: $('audioFill'),
  resultsArea: $('resultsArea'),
  emptyState: $('emptyState'),
  resultsList: $('resultsList'),
  startBtn: $('startBtn'),
  stopBtn: $('stopBtn'),
  clearBtn: $('clearBtn'),
  settingsBtn: $('settingsBtn'),
};

// ── 状态 ─────────────────────────────────────────────────
let isRecording = false;
let statusTimer = null;
let resultsTimer = null;
let lastResultId = 0;

// ── 格式化时间 ───────────────────────────────────────────
function fmtTime(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

// ── 更新 UI ──────────────────────────────────────────────
function setRecording(recording) {
  isRecording = recording;
  el.statusDot.classList.toggle('recording', recording);
  el.startBtn.style.display = recording ? 'none' : 'block';
  el.stopBtn.style.display = recording ? 'block' : 'none';
  if (!recording) {
    el.timer.textContent = '00:00';
    el.audioFill.style.width = '0%';
  }
}

function addResult(item) {
  if (el.emptyState) el.emptyState.style.display = 'none';

  const card = document.createElement('div');
  card.className = 'result-card';
  card.innerHTML = `
    <div class="result-text">${escapeHtml(item.text)}</div>
    <div class="result-meta">
      <span class="result-id">#${item.id}</span>
      <span>${item.timestamp}</span>
    </div>
  `;
  el.resultsList.appendChild(card);
  el.resultsArea.scrollTop = el.resultsArea.scrollHeight;
}

function clearResults() {
  el.resultsList.innerHTML = '';
  el.emptyState.style.display = 'flex';
  lastResultId = 0;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ── API 请求 ─────────────────────────────────────────────
async function apiPost(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: 'POST' });
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  return res.json();
}

// ── 轮询状态 ─────────────────────────────────────────────
async function pollStatus() {
  try {
    const data = await apiGet('/api/status');
    el.timer.textContent = fmtTime(data.elapsed || 0);
    el.audioFill.style.width = `${data.level || 0}%`;

    if (data.is_recording !== isRecording) {
      setRecording(data.is_recording);
    }
  } catch (e) {
    console.warn('状态轮询失败', e);
  }
}

async function pollResults() {
  try {
    const data = await apiGet('/api/results');
    const newItems = (data.results || []).filter(r => r.id > lastResultId);
    newItems.forEach(item => {
      addResult(item);
      lastResultId = item.id;
    });
  } catch (e) {
    console.warn('结果轮询失败', e);
  }
}

// ── 按钮事件 ─────────────────────────────────────────────
el.startBtn.addEventListener('click', async () => {
  el.startBtn.disabled = true;
  try {
    const data = await apiPost('/api/start');
    if (data.success) {
      setRecording(true);
      startPolling();
    } else {
      alert(data.message || '启动失败');
    }
  } catch (e) {
    alert('网络错误：' + e.message);
  } finally {
    el.startBtn.disabled = false;
  }
});

el.stopBtn.addEventListener('click', async () => {
  try {
    await apiPost('/api/stop');
    setRecording(false);
    stopPolling();
  } catch (e) {
    alert('网络错误：' + e.message);
  }
});

el.clearBtn.addEventListener('click', async () => {
  try {
    await apiPost('/api/clear');
    clearResults();
  } catch (e) {
    console.warn('清空失败', e);
  }
});

el.settingsBtn.addEventListener('click', () => {
  // 预留：设置面板
  alert('设置功能暂未实现');
});

// ── 轮询控制 ─────────────────────────────────────────────
function startPolling() {
  if (statusTimer) clearInterval(statusTimer);
  if (resultsTimer) clearInterval(resultsTimer);
  statusTimer = setInterval(pollStatus, 200);
  resultsTimer = setInterval(pollResults, 500);
}

function stopPolling() {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
  if (resultsTimer) { clearInterval(resultsTimer); resultsTimer = null; }
}

// ── 初始化 ───────────────────────────────────────────────
function init() {
  console.log('粤语实时翻译前端已加载');
  // 先拉一次状态
  pollStatus();
  pollResults();
}

init();
