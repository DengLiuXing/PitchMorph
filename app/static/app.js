/* PitchMorph front-end: add tasks → watch the list → open a finished one.
   No build step, no dependencies. */

const $ = (sel, root = document) => root.querySelector(sel);

const STEM_LABEL = { drums: 'Drums', bass: 'Bass', other: 'Other', vocals: 'Vocals' };
const STEM_ORDER = ['drums', 'bass', 'other', 'vocals'];
const ACTIVE = new Set(['queued', 'downloading', 'loading', 'separating']);

const el = {
  stage: $('#stage'),
  views: { home: $('#view-home'), workbench: $('#view-workbench') },

  dropzone: $('#dropzone'),
  fileInput: $('#file-input'),
  dropFormats: $('#drop-formats'),
  dropLimit: $('#drop-limit'),
  tiers: $('#tiers'),
  btnPick: $('#btn-pick'),
  uploadError: $('#upload-error'),

  taskGroups: $('#task-groups'),
  taskEmpty: $('#task-empty'),
  taskSummary: $('#task-summary'),

  tracks: $('#tracks'),
  playhead: $('#playhead'),
  wbTitle: $('#wb-title'),
  wbSub: $('#wb-sub'),
  btnBack: $('#btn-back'),
  btnZip: $('#btn-zip'),
  btnPlay: $('#btn-play'),
  btnLoop: $('#btn-loop'),
  timeNow: $('#time-now'),
  timeTotal: $('#time-total'),
  seek: $('#seek'),
  seekFill: $('#seek-fill'),
  seekKnob: $('#seek .seek-knob'),

  chipProvider: $('#chip-provider'),
  chipWeights: $('#chip-weights'),
  btnHealth: $('#btn-health'),
  healthDialog: $('#health-dialog'),
  healthList: $('#health-list'),
  healthNote: $('#health-note'),
};

const state = {
  tiers: [],
  tier: null,
  health: null,
  jobs: new Map(),
  refs: new Map(),
  signature: '',
  view: 'home',
  poll: null,
  uploading: false,
  tracks: [],
  ctx: null,
  playing: false,
  loop: false,
  duration: 0,
  openId: null,
  raf: null,
  headOffset: 0,
  waveWidth: 0,
  dragging: false,
};

/* ── helpers ──────────────────────────────────────────────────── */

const fmtTime = (sec) => {
  const s = Math.max(0, sec || 0);
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(1).padStart(4, '0')}`;
};

const fmtClock = (sec) => {
  const s = Math.max(0, Math.round(sec || 0));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

const fmtBytes = (bytes) => {
  if (!bytes) return '—';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
};

const tierLabel = (key) => state.tiers.find((t) => t.key === key)?.label ?? key;

/** Original filename without extension, stripped of characters Windows rejects. */
const baseName = (filename) => {
  const stem = String(filename || 'audio').replace(/\.[^.]+$/, '');
  return stem.replace(/[\\/:*?"<>|]+/g, '_').trim() || 'audio';
};

const showError = (message) => {
  el.uploadError.textContent = message;
  el.uploadError.hidden = false;
};

const clearError = () => {
  el.uploadError.textContent = '';
  el.uploadError.hidden = true;
};

function setView(name) {
  state.view = name;
  el.stage.dataset.view = name;
  Object.entries(el.views).forEach(([key, node]) => {
    node.classList.toggle('is-active', key === name);
  });
}

function stageText(job) {
  switch (job.stage) {
    case 'queued': return '排队中';
    case 'downloading': return `下载权重 ${Math.round((job.progress || 0) * 100)}%`;
    case 'loading': return '加载模型';
    case 'separating': {
      const pct = Math.round((job.progress || 0) * 100);
      if (job.total > 1) return `分离 ${job.done}/${job.total} · ${pct}%`;
      return `分离 ${pct}%`;
    }
    case 'done': return `已完成 · ${Object.keys(job.stems || {}).length} 轨`;
    case 'canceled': return '已取消';
    case 'error': return job.error || '失败';
    default: return job.stage;
  }
}

/* ── bootstrap ────────────────────────────────────────────────── */

async function boot() {
  setView('home');
  try {
    await loadModels();
    await loadHealth();
  } catch (err) {
    showError(`无法连接后端：${err.message}`);
  }
  const active = await refresh();
  schedulePoll(active);
}

async function loadModels() {
  const res = await fetch('/api/models');
  if (!res.ok) throw new Error(`/api/models ${res.status}`);
  state.tiers = await res.json();
  state.tier = state.tiers[0]?.key ?? null;
  renderTiers();
}

async function loadHealth() {
  const res = await fetch('/api/health');
  if (!res.ok) throw new Error(`/api/health ${res.status}`);
  const h = await res.json();
  state.health = h;

  el.chipProvider.textContent = (h.active_providers?.[0] || 'CPUExecutionProvider').replace('ExecutionProvider', '');
  el.chipProvider.dataset.tone = 'amber';

  const cached = state.tiers.filter((t) => t.cached);
  el.chipWeights.textContent = cached.length
    ? `权重 ${cached.length}/${state.tiers.length} 已缓存`
    : '权重未下载';
  el.chipWeights.dataset.tone = cached.length ? 'teal' : '';

  el.fileInput.accept = `${h.supported_exts.map((e) => `.${e}`).join(',')},audio/*,video/*`;

  const exts = h.supported_exts.map((e) => e.toUpperCase());
  el.dropFormats.textContent = exts.length > 10
    ? `${exts.slice(0, 10).join(' · ')} 等 ${exts.length} 种，含视频`
    : exts.join(' · ');

  if (el.dropLimit) {
    el.dropLimit.textContent =
      `≤ ${Math.round(h.max_duration_sec / 60)}:00 · ≤ ${h.max_upload_mb} MB · 支持批量拖入`;
  }
}

function renderTiers() {
  el.tiers.innerHTML = '';
  state.tiers.forEach((tier) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'seg';
    btn.setAttribute('role', 'radio');
    btn.setAttribute('aria-checked', String(tier.key === state.tier));
    btn.dataset.key = tier.key;
    if (tier.cached) btn.dataset.cached = 'true';

    const label = document.createElement('span');
    label.className = 'seg-label';
    label.textContent = tier.label;
    const meta = document.createElement('span');
    meta.className = 'seg-meta';
    meta.textContent = `${tier.download_mb} MB · ${tier.eta}`;
    btn.append(label, meta);

    btn.addEventListener('click', () => selectTier(tier.key));
    el.tiers.appendChild(btn);
  });
  syncTierButton();
}

function selectTier(key) {
  state.tier = key;
  el.tiers.querySelectorAll('.seg').forEach((node) => {
    node.setAttribute('aria-checked', String(node.dataset.key === key));
  });
  syncTierButton();
}

function syncTierButton() {
  const tier = state.tiers.find((t) => t.key === state.tier);
  el.btnPick.textContent = tier ? `选择文件 · ${tier.stems.length} 轨` : '选择文件';
}

/* ── upload ───────────────────────────────────────────────────── */

function bindUpload() {
  el.dropzone.addEventListener('click', () => el.fileInput.click());
  el.dropzone.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      el.fileInput.click();
    }
  });
  el.btnPick.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', () => {
    const files = [...(el.fileInput.files || [])];
    el.fileInput.value = '';
    if (files.length) upload(files);
  });

  ['dragenter', 'dragover'].forEach((type) => {
    el.dropzone.addEventListener(type, (ev) => {
      ev.preventDefault();
      el.dropzone.classList.add('is-drag');
    });
  });
  ['dragleave', 'dragend'].forEach((type) => {
    el.dropzone.addEventListener(type, () => el.dropzone.classList.remove('is-drag'));
  });
  el.dropzone.addEventListener('drop', (ev) => {
    ev.preventDefault();
    el.dropzone.classList.remove('is-drag');
    const files = [...(ev.dataTransfer?.files || [])];
    if (files.length) upload(files);
  });
}

async function upload(files) {
  if (state.uploading) return;
  state.uploading = true;
  clearError();
  el.btnPick.disabled = true;

  const failures = [];
  for (const [index, file] of files.entries()) {
    el.btnPick.textContent = files.length > 1
      ? `上传中 ${index + 1}/${files.length}…`
      : '上传中…';
    try {
      await uploadOne(file);
    } catch (err) {
      failures.push(`${file.name}：${err.message}`);
    }
  }

  el.btnPick.disabled = false;
  syncTierButton();
  state.uploading = false;

  if (failures.length) showError(failures.join('；'));
  const active = await refresh();
  schedulePoll(active);
}

async function uploadOne(file) {
  const form = new FormData();
  form.append('file', file);
  form.append('tier', state.tier);

  const res = await fetch('/api/jobs', { method: 'POST', body: form });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* keep the status code */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ── task list ────────────────────────────────────────────────── */

async function refresh() {
  try {
    const res = await fetch('/api/jobs');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const list = await res.json();
    state.jobs = new Map(list.map((job) => [job.id, job]));
    renderTasks();
    return list.some((job) => ACTIVE.has(job.stage));
  } catch (err) {
    console.error('refresh failed', err);
    showError(`刷新任务列表失败：${err.message}`);
    return false;
  }
}

function schedulePoll(hasActive) {
  if (state.poll) clearTimeout(state.poll);
  state.poll = null;
  if (!hasActive || state.view !== 'home') return;
  state.poll = setTimeout(async () => {
    const active = await refresh();
    schedulePoll(active);
  }, 900);
}

function renderTasks() {
  const all = [...state.jobs.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const groups = [
    { key: 'running', title: '进行中', items: all.filter((j) => ACTIVE.has(j.stage)) },
    { key: 'done', title: '已完成', items: all.filter((j) => j.stage === 'done') },
    { key: 'other', title: '失败 / 已取消', items: all.filter((j) => !ACTIVE.has(j.stage) && j.stage !== 'done') },
  ].filter((group) => group.items.length);

  el.taskEmpty.hidden = all.length > 0;

  const running = groups.find((g) => g.key === 'running')?.items.length ?? 0;
  el.taskSummary.textContent = all.length
    ? `${all.length} 个任务${running ? ` · ${running} 进行中` : ''}`
    : '';

  const signature = JSON.stringify(groups.map((g) => [g.key, g.items.map((j) => j.id)]));
  if (signature !== state.signature) {
    rebuild(groups);
    state.signature = signature;
    return;
  }
  all.forEach(updateTask);
}

function rebuild(groups) {
  el.taskGroups.innerHTML = '';
  state.refs.clear();

  groups.forEach((group) => {
    const section = document.createElement('section');
    section.className = 'task-group';

    const head = document.createElement('div');
    head.className = 'task-group-head';
    const title = document.createElement('span');
    title.textContent = group.title;
    const count = document.createElement('span');
    count.className = 'mono dim';
    count.textContent = String(group.items.length);
    head.append(title, count);

    const list = document.createElement('ul');
    list.className = 'task-list';
    group.items.forEach((job) => list.appendChild(buildTask(job)));

    section.append(head, list);
    el.taskGroups.appendChild(section);
  });

  groups.flatMap((g) => g.items).forEach(updateTask);
}

function buildTask(job) {
  const root = document.createElement('li');
  root.className = 'task';
  root.dataset.id = job.id;

  const main = document.createElement('div');
  main.className = 'task-main';

  const line = document.createElement('div');
  line.className = 'task-line';

  const status = document.createElement('span');
  status.className = 'task-status';

  const name = document.createElement('span');
  name.className = 'task-name';
  name.textContent = job.filename;
  name.title = job.filename;

  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = tierLabel(job.tier);

  const meta = document.createElement('span');
  meta.className = 'task-meta mono';

  line.append(status, name, tag, meta);

  const bar = document.createElement('div');
  bar.className = 'task-bar';
  const fill = document.createElement('i');
  bar.appendChild(fill);

  main.append(line, bar);

  const actions = document.createElement('div');
  actions.className = 'task-actions';

  root.append(main, actions);
  state.refs.set(job.id, { root, status, meta, bar, fill, actions, signature: '' });
  return root;
}

function updateTask(job) {
  const ref = state.refs.get(job.id);
  if (!ref) return;

  const running = ACTIVE.has(job.stage);
  const stateName = running ? 'running' : job.stage;
  const signature = `${stateName}|${job.cancel_requested}|${job.stage}`;

  ref.root.dataset.state = stateName;
  ref.status.textContent = stageText(job);
  ref.status.title = job.stage === 'error' ? (job.error || '') : '';

  const chips = [];
  if (job.duration_sec) chips.push(fmtClock(job.duration_sec));
  if (job.size_bytes) chips.push(fmtBytes(job.size_bytes));
  if (job.stage === 'queued') {
    chips.push(`等待 ${fmtClock(job.wait_sec)}`);
  } else if (running) {
    chips.push(`已用 ${fmtClock(job.elapsed_sec)}`);
  } else if (job.elapsed_sec) {
    chips.push(`耗时 ${fmtClock(job.elapsed_sec)}`);
  }
  ref.meta.textContent = chips.join(' · ');

  // A queued job has nothing to measure yet, so no bar at all.
  const showBar = ['downloading', 'loading', 'separating'].includes(job.stage);
  ref.bar.hidden = !showBar;
  if (showBar) {
    const determinate = ['downloading', 'separating'].includes(job.stage) && (job.progress || 0) > 0;
    ref.fill.style.width = determinate ? `${Math.round((job.progress || 0) * 100)}%` : '';
    ref.fill.classList.toggle('is-indeterminate', !determinate);
  }

  if (ref.signature !== signature) {
    ref.signature = signature;
    ref.actions.innerHTML = '';

    const add = (text, act, kind = 'ghost') => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `btn btn-sm btn-${kind}`;
      btn.textContent = text;
      btn.dataset.act = act;
      btn.addEventListener('click', () => onAction(act, job));
      ref.actions.appendChild(btn);
      return btn;
    };

    if (running) {
      const cancel = add(job.cancel_requested ? '取消中…' : '取消', 'cancel');
      cancel.disabled = Boolean(job.cancel_requested);
    } else if (job.stage === 'done') {
      add('打开', 'open', 'primary');
      add('下载', 'download');
      add('删除', 'delete');
    } else {
      add('重试', 'retry', 'primary');
      add('删除', 'delete');
    }
  }
}

async function onAction(act, job) {
  if (act === 'open') return openJob(job.id);
  if (act === 'download') {
    window.location.href = `/api/jobs/${job.id}/download`;
    return;
  }

  if (act === 'cancel') {
    await fetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' }).catch(() => {});
  } else if (act === 'retry') {
    const res = await fetch(`/api/jobs/${job.id}/retry`, { method: 'POST' });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json()).detail || detail;
      } catch { /* keep the status code */ }
      showError(`重试失败：${detail}`);
      return;
    }
    clearError();
  } else if (act === 'delete') {
    await fetch(`/api/jobs/${job.id}`, { method: 'DELETE' }).catch(() => {});
    if (state.openId === job.id) backToHome();
  }

  const active = await refresh();
  schedulePoll(active);
}

/* ── workbench ────────────────────────────────────────────────── */

function stemsOf(job) {
  const keys = Object.keys(job.stems || {});
  return STEM_ORDER.filter((k) => keys.includes(k)).concat(keys.filter((k) => !STEM_ORDER.includes(k)));
}

async function openJob(jobId) {
  const job = state.jobs.get(jobId);
  if (!job || job.stage !== 'done') return;
  const names = stemsOf(job);
  if (!names.length) {
    showError('该任务没有产出轨道');
    return;
  }

  state.openId = jobId;
  if (state.poll) clearTimeout(state.poll);
  state.poll = null;

  el.wbTitle.textContent = job.filename;
  el.wbSub.textContent = `${names.length} 轨 · 44.1 kHz / 16-bit · ${fmtClock(job.duration_sec)}`;
  // No `download` attribute here on purpose: the server sends
  // Content-Disposition with “<source>-stems.zip”, and the attribute would
  // override that with the URL's last path segment.
  el.btnZip.href = `/api/jobs/${job.id}/download`;

  buildTracks(names, job.stems);
  setView('workbench');
  measureWaveArea();
  await setupAudio(names, job.stems);
  await drawWaveforms();
  requestAnimationFrame(() => {
    measureWaveArea();
    renderPosition(0);
  });
}

function backToHome() {
  teardownPlayback();
  el.tracks.querySelectorAll('.track').forEach((node) => node.remove());
  state.openId = null;
  setView('home');
  refresh().then(schedulePoll);
}

function buildTracks(names, urls) {
  teardownPlayback();
  el.tracks.querySelectorAll('.track').forEach((node) => node.remove());

  // Prefix every download with the source track name so two jobs never collide
  // in the browser's download folder.
  const base = baseName(state.jobs.get(state.openId)?.filename);

  state.tracks = names.map((name) => {
    const node = document.createElement('article');
    node.className = 'track is-pending';
    node.dataset.stem = name;

    const head = document.createElement('div');
    head.className = 'track-head';
    head.innerHTML = `
      <span class="track-name">${STEM_LABEL[name] ?? name}</span>
      <div class="track-ms">
        <button class="ms" data-act="mute" aria-pressed="false" title="静音">M</button>
        <button class="ms" data-act="solo" aria-pressed="false" title="独奏">S</button>
      </div>
      <input class="track-vol" type="range" min="0" max="100" value="100"
             aria-label="${STEM_LABEL[name] ?? name} 音量">
      <a class="track-dl" href="${urls[name]}" download="${base}-${name}.wav"
         title="下载 ${base}-${name}.wav">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg>
      </a>`;

    const wave = document.createElement('div');
    wave.className = 'wave';
    wave.innerHTML = '<canvas class="wave-base"></canvas><canvas class="wave-lit"></canvas>';

    node.append(head, wave);
    el.tracks.insertBefore(node, el.playhead);

    const track = {
      name,
      url: urls[name],
      el: node,
      wave,
      canvasBase: $('.wave-base', node),
      canvasLit: $('.wave-lit', node),
      audio: null,
      gain: null,
      muted: false,
      solo: false,
      volume: 1,
    };

    $('[data-act="mute"]', node).addEventListener('click', (ev) => {
      track.muted = !track.muted;
      ev.currentTarget.setAttribute('aria-pressed', String(track.muted));
      applyMix();
    });
    $('[data-act="solo"]', node).addEventListener('click', (ev) => {
      track.solo = !track.solo;
      ev.currentTarget.setAttribute('aria-pressed', String(track.solo));
      applyMix();
    });
    $('.track-vol', node).addEventListener('input', (ev) => {
      track.volume = Number(ev.target.value) / 100;
      applyMix();
    });

    return track;
  });
}

async function setupAudio(names, urls) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  state.ctx = new AudioCtx();

  state.tracks.forEach((track) => {
    const audio = new Audio();
    audio.preload = 'auto';
    audio.src = urls[track.name];
    const source = state.ctx.createMediaElementSource(audio);
    const gain = state.ctx.createGain();
    source.connect(gain).connect(state.ctx.destination);
    track.audio = audio;
    track.gain = gain;
  });

  await Promise.all(state.tracks.map((track) => new Promise((resolve) => {
    if (track.audio.readyState >= 1) return resolve();
    track.audio.addEventListener('loadedmetadata', resolve, { once: true });
    track.audio.addEventListener('error', resolve, { once: true });
    setTimeout(resolve, 4000);
  })));

  const job = state.jobs.get(state.openId);
  state.duration = Math.max(
    ...state.tracks.map((t) => t.audio.duration || 0),
    job?.duration_sec || 0,
  );
  el.timeTotal.textContent = fmtTime(state.duration);
  applyMix();
}

function applyMix() {
  const anySolo = state.tracks.some((t) => t.solo);
  state.tracks.forEach((track) => {
    const audible = !track.muted && (!anySolo || track.solo);
    if (track.gain) track.gain.gain.value = audible ? track.volume : 0;
    track.el.dataset.muted = String(track.muted);
    track.el.dataset.dimmed = String(!audible);
  });
}

/* ── playback ─────────────────────────────────────────────────── */

async function togglePlay() {
  if (!state.tracks.length) return;
  if (state.playing) pauseAll();
  else await playAll();
}

async function playAll() {
  await state.ctx?.resume();
  const at = state.tracks[0].audio.currentTime;
  state.tracks.forEach((track) => {
    if (Math.abs(track.audio.currentTime - at) > 0.05) track.audio.currentTime = at;
  });
  await Promise.all(state.tracks.map((t) => t.audio.play().catch(() => {})));
  state.playing = true;
  el.btnPlay.classList.add('is-playing');
  el.btnPlay.setAttribute('aria-label', '暂停');
  startClock();
}

function pauseAll() {
  state.tracks.forEach((track) => track.audio?.pause());
  state.playing = false;
  el.btnPlay.classList.remove('is-playing');
  el.btnPlay.setAttribute('aria-label', '播放');
}

function seekTo(seconds) {
  const target = Math.min(Math.max(0, seconds), state.duration || 0);
  state.tracks.forEach((track) => {
    if (track.audio) track.audio.currentTime = target;
  });
  renderPosition(target);
}

function startClock() {
  if (state.raf) cancelAnimationFrame(state.raf);
  const frame = () => {
    const master = state.tracks[0]?.audio;
    if (!master) return;
    const now = master.currentTime;
    state.tracks.slice(1).forEach((track) => {
      if (Math.abs(track.audio.currentTime - now) > 0.08) track.audio.currentTime = now;
    });
    renderPosition(now);
    if (state.playing && state.loop && state.duration && now >= state.duration - 0.08) seekTo(0);
    state.raf = requestAnimationFrame(frame);
  };
  state.raf = requestAnimationFrame(frame);
}

function renderPosition(seconds) {
  const ratio = state.duration ? Math.min(1, seconds / state.duration) : 0;
  const pct = `${(ratio * 100).toFixed(3)}%`;

  el.seekFill.style.width = pct;
  el.seekKnob.style.left = pct;
  el.timeNow.textContent = fmtTime(seconds);
  el.seek.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));

  el.playhead.classList.toggle('is-visible', ratio > 0 || state.playing);
  el.playhead.style.transform = `translateX(${state.headOffset + ratio * state.waveWidth}px)`;

  state.tracks.forEach((track) => track.wave.style.setProperty('--p', pct));
}

/* ── waveforms ────────────────────────────────────────────────── */

function measureWaveArea() {
  const wave = el.tracks.querySelector('.wave');
  if (!wave) return;
  const box = wave.getBoundingClientRect();
  const base = el.tracks.getBoundingClientRect();
  state.headOffset = box.left - base.left;
  state.waveWidth = box.width;
}

/** Parse a PCM-16 RIFF/WAV buffer into normalised peak buckets. */
function wavPeaks(buffer, buckets) {
  const view = new DataView(buffer);
  if (buffer.byteLength < 44 || view.getUint32(0, false) !== 0x52494646) return null;

  let offset = 12;
  let channels = 0;
  let bits = 0;
  let dataStart = 0;
  let dataLength = 0;

  while (offset + 8 <= buffer.byteLength) {
    const id = view.getUint32(offset, false);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === 0x666d7420) {
      channels = view.getUint16(body + 2, true);
      bits = view.getUint16(body + 14, true);
    } else if (id === 0x64617461) {
      dataStart = body;
      dataLength = Math.min(size, buffer.byteLength - body);
      break;
    }
    offset = body + size + (size % 2);
  }

  if (!channels || bits !== 16 || !dataStart) return null;

  const frames = Math.floor(dataLength / (2 * channels));
  if (!frames) return null;

  const step = Math.max(1, Math.floor(frames / buckets));
  const peaks = new Float32Array(buckets);

  for (let b = 0; b < buckets; b += 1) {
    const from = b * step;
    const to = Math.min(frames, from + step);
    let peak = 0;
    for (let f = from; f < to; f += 1) {
      for (let c = 0; c < channels; c += 1) {
        const v = Math.abs(view.getInt16(dataStart + (f * channels + c) * 2, true));
        if (v > peak) peak = v;
      }
    }
    peaks[b] = peak / 32768;
  }
  return peaks;
}

const BASE_COLORS = {
  edge: 'rgba(138, 148, 166, 0.30)',
  core: 'rgba(138, 148, 166, 0.52)',
  flat: 'rgba(138, 148, 166, 0.22)',
};

const LIT_COLORS = {
  edge: 'rgba(242, 169, 59, 0.55)',
  core: 'rgba(255, 196, 107, 0.98)',
  flat: 'rgba(242, 169, 59, 0.4)',
};

function paintWave(canvas, peaks, colors) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = canvas.clientWidth || 1;
  const height = canvas.clientHeight || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!peaks) {
    ctx.fillStyle = colors.flat;
    ctx.fillRect(0, height / 2 - 1, width, 2);
    return;
  }

  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, colors.edge);
  gradient.addColorStop(0.5, colors.core);
  gradient.addColorStop(1, colors.edge);
  ctx.fillStyle = gradient;

  const bar = 2;
  const gap = 1;
  const mid = height / 2;
  const maxAmp = height * 0.46;

  for (let x = 0; x < width; x += bar + gap) {
    const index = Math.min(peaks.length - 1, Math.floor((x / width) * peaks.length));
    const amp = Math.max(0.012, peaks[index]) * maxAmp;
    ctx.beginPath();
    ctx.roundRect(x, mid - amp, bar, amp * 2, 1);
    ctx.fill();
  }
}

function repaintTrack(track) {
  paintWave(track.canvasBase, track.peaks, BASE_COLORS);
  paintWave(track.canvasLit, track.peaks, LIT_COLORS);
}

async function drawWaveforms() {
  const buckets = Math.max(80, Math.floor((state.waveWidth || 480) / 3));
  for (const track of state.tracks) {
    try {
      const res = await fetch(track.url);
      track.peaks = wavPeaks(await res.arrayBuffer(), buckets);
    } catch {
      track.peaks = null;
    }
    repaintTrack(track);
    track.el.classList.remove('is-pending');
  }
  renderPosition(state.tracks[0]?.audio?.currentTime || 0);
}

/* ── teardown ─────────────────────────────────────────────────── */

function teardownPlayback() {
  if (state.raf) cancelAnimationFrame(state.raf);
  state.raf = null;
  state.playing = false;
  state.tracks.forEach((track) => {
    try {
      track.audio?.pause();
    } catch { /* element may already be detached */ }
  });
  state.tracks = [];
  if (state.ctx) {
    state.ctx.close().catch(() => {});
    state.ctx = null;
  }
  el.btnPlay.classList.remove('is-playing');
  el.timeNow.textContent = '00:00.0';
  el.seekFill.style.width = '0%';
}

/* ── health sheet ─────────────────────────────────────────────── */

async function openHealth() {
  let data = state.health;
  try {
    const res = await fetch('/api/health');
    if (res.ok) data = await res.json();
  } catch { /* fall back to the cached snapshot */ }
  if (!data) return;

  const source = {
    env: '（PITCHMORPH_FFMPEG 指定）',
    path: '（系统 PATH）',
    bundled: '（内置）',
  }[data.ffmpeg_source] || '';

  const rows = [
    ['Python', data.python],
    ['ONNX Runtime', data.onnxruntime],
    ['可用执行器', (data.available_providers || []).join(', ')],
    ['当前执行器', (data.active_providers || []).join(', ')],
    ['ffmpeg', data.ffmpeg_version ? `${data.ffmpeg_version} ${source}` : '不可用'],
    ['权重目录', data.model_dir],
    ['支持格式', data.supported_exts.join(' · ')],
    ['上传上限', `${data.max_upload_mb} MB / ${Math.round(data.max_duration_sec / 60)} 分钟`],
  ];
  el.healthList.innerHTML = '';
  rows.forEach(([key, value]) => {
    const dt = document.createElement('dt');
    dt.textContent = key;
    const dd = document.createElement('dd');
    dd.textContent = value ?? '—';
    el.healthList.append(dt, dd);
  });

  el.healthNote.textContent = data.ffmpeg
    ? 'ffmpeg 只在 libsndfile 读不了容器时启用（mp4 / m4s / mkv / m4a 等）；wav / flac / mp3 不走它。'
    : '未检测到 ffmpeg：wav / flac / mp3 仍然可用，视频与 m4a 会被拒绝。重装 imageio-ffmpeg 或把 ffmpeg 放进 PATH 后重启即可。';

  if (typeof el.healthDialog.showModal === 'function') el.healthDialog.showModal();
}

/* ── wiring ───────────────────────────────────────────────────── */

function bindWorkbench() {
  el.btnPlay.addEventListener('click', togglePlay);
  el.btnBack.addEventListener('click', backToHome);

  el.btnLoop.addEventListener('click', () => {
    state.loop = !state.loop;
    el.btnLoop.setAttribute('aria-pressed', String(state.loop));
  });

  const seekFrom = (ev) => {
    const rect = el.seek.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
    seekTo(ratio * (state.duration || 0));
  };

  el.seek.addEventListener('pointerdown', (ev) => {
    state.dragging = true;
    el.seek.setPointerCapture(ev.pointerId);
    seekFrom(ev);
  });
  el.seek.addEventListener('pointermove', (ev) => {
    if (state.dragging) seekFrom(ev);
  });
  el.seek.addEventListener('pointerup', (ev) => {
    state.dragging = false;
    el.seek.releasePointerCapture(ev.pointerId);
  });
  el.seek.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    ev.preventDefault();
    const delta = ev.key === 'ArrowRight' ? 3 : -3;
    seekTo((state.tracks[0]?.audio.currentTime || 0) + delta);
  });

  window.addEventListener('resize', () => {
    if (!state.tracks.length) return;
    measureWaveArea();
    state.tracks.forEach(repaintTrack);
    renderPosition(state.tracks[0]?.audio?.currentTime || 0);
  });
}

window.addEventListener('keydown', (ev) => {
  if (ev.code !== 'Space' || state.view !== 'workbench') return;
  const tag = document.activeElement?.tagName;
  if (['INPUT', 'TEXTAREA', 'BUTTON', 'A'].includes(tag)) return;
  if (!state.tracks.length) return;
  ev.preventDefault();
  togglePlay();
});

window.addEventListener('focus', () => {
  if (state.view === 'home') refresh().then(schedulePoll);
});

bindUpload();
bindWorkbench();
el.btnHealth.addEventListener('click', openHealth);
boot();
