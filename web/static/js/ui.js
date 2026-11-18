// UI toolkit: safe DOM builder, icons, toasts, modals, formatters, shared components.

const ICONS = {
  play: '<path d="M8 5l11 7-11 7z" fill="currentColor" stroke="none"/>',
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  library: '<path d="M4 19.5V5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-1.5z"/><path d="M8 7h6M8 11h6"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15v3M12 10v8M17 6v12"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  resume: '<path d="M8 5l11 7-11 7z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  retry: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
  trash: '<path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  youtube: '<path d="M22.5 6.4a2.8 2.8 0 0 0-2-2C18.8 4 12 4 12 4s-6.8 0-8.5.4a2.8 2.8 0 0 0-2 2A29 29 0 0 0 1 12a29 29 0 0 0 .5 5.6 2.8 2.8 0 0 0 2 2c1.7.4 8.5.4 8.5.4s6.8 0 8.5-.4a2.8 2.8 0 0 0 2-2A29 29 0 0 0 23 12a29 29 0 0 0-.5-5.6z"/><path d="m10 15 5-3-5-3z" fill="currentColor"/>',
  instagram: '<rect x="2" y="2" width="20" height="20" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M17.5 6.5h.01"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.9A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
};

export function icon(name, cls = '') {
  const span = document.createElement('span');
  span.style.display = 'inline-flex';
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${cls}" aria-hidden="true">${ICONS[name] || ''}</svg>`;
  return span.firstChild;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    if (!el.firstChild) el.appendChild(icon(el.dataset.icon));
  });
}

// h('div.card', {onclick}, child, 'text', [children]) - text is always set via textContent (XSS-safe)
export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (attrs !== undefined && attrs !== null && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs); attrs = null;
  }
  let deferredValue;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'value') deferredValue = v; // set after children exist (select/textarea)
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && !['list', 'form', 'type'].includes(k) && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  if (deferredValue !== undefined) el.value = deferredValue;
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

// ---------------------------------------------------------------- feedback
export function toast(message, kind = 'info', ms = 4200) {
  const box = document.getElementById('toasts');
  const t = h('div.toast', { class: kind }, icon(kind === 'good' ? 'check' : kind === 'bad' ? 'alert' : 'info'), h('div', message));
  box.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); }, ms);
}

export function modal({ title, body, actions = [], wide = false }) {
  return new Promise((resolve) => {
    const bg = h('div.modal-bg');
    const close = (v) => { bg.remove(); document.removeEventListener('keydown', esc); resolve(v); };
    const esc = (e) => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', esc);
    const card = h('div.card.modal', { style: wide ? { width: 'min(820px, 100%)' } : null },
      h('div.card-h', h('h3', title), h('div', { style: { flex: 1 } }),
        h('button.btn.icon.ghost.sm', { onclick: () => close(null), 'aria-label': 'Close' }, icon('x'))),
      h('div.card-b', body),
      actions.length ? h('div.modal-actions', actions.map((a) => h(`button.btn${a.kind ? '.' + a.kind : ''}`, {
        onclick: async (e) => {
          if (!a.onClick) return close(a.value);
          const btn = e.currentTarget; btn.classList.add('loading');
          try { const r = await a.onClick(); if (r !== false) close(r ?? a.value); }
          catch (err) { toast(err.message, 'bad'); }
          finally { btn.classList.remove('loading'); }
        } }, a.label))) : null);
    bg.appendChild(card);
    bg.addEventListener('mousedown', (e) => { if (e.target === bg) close(null); });
    document.body.appendChild(bg);
    const first = card.querySelector('input, textarea, select');
    if (first) setTimeout(() => first.focus(), 30);
  });
}

export const confirmDialog = (title, message, label = 'Confirm', kind = 'primary') =>
  modal({ title, body: h('p.dim', { style: { margin: 0 } }, message),
    actions: [{ label: 'Cancel', value: false, kind: 'ghost' }, { label, value: true, kind }] });

// run an async action with button spinner + toast on error
export async function busy(btn, fn, okMsg) {
  btn?.classList.add('loading');
  try { const r = await fn(); if (okMsg) toast(okMsg, 'good'); return r; }
  catch (e) { toast(e.message, 'bad', 6500); throw e; }
  finally { btn?.classList.remove('loading'); }
}

// ---------------------------------------------------------------- formatters
export const fmt = {
  n: (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-US')),
  compact: (v) => (v === null || v === undefined ? '—' : Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v)),
  pct: (v) => (v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%'),
  dur: (s) => { if (s === null || s === undefined) return '—'; s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; },
  ts: (s) => { s = Math.max(0, s || 0); const hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = (s % 60).toFixed(1);
    return (hh ? hh + ':' + String(mm).padStart(2, '0') : mm) + ':' + ss.padStart(4, '0'); },
  date: (iso) => iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—',
  rel: (iso) => {
    if (!iso) return '—';
    const d = (new Date(iso) - Date.now()) / 1000, a = Math.abs(d);
    const [v, u] = a < 60 ? [a, 'second'] : a < 3600 ? [a / 60, 'minute'] : a < 86400 ? [a / 3600, 'hour'] : [a / 86400, 'day'];
    return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(Math.round(d < 0 ? -v : v), u);
  },
  cat: (c) => (c || 'other').replace(/_/g, ' '),
};

export function inTz(iso, tz) {
  return new Date(iso).toLocaleString('en-US', { timeZone: tz, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// ---------------------------------------------------------------- shared components
const STATUS = {
  queued: ['', 'Queued'], running: ['info', 'Running'], paused: ['warn', 'Paused'], done: ['good', 'Done'],
  failed: ['bad', 'Failed'], cancelled: ['', 'Cancelled'], interrupted: ['warn', 'Interrupted'],
  pending: ['warn', 'Needs review'], approved: ['good', 'Approved'], rejected: ['bad', 'Rejected'],
  scheduled: ['info', 'Scheduled'], uploading: ['info', 'Uploading'], uploaded: ['accent', 'Uploaded · scheduled'],
  published: ['good', 'Published'], blocked: ['bad', 'Blocked'], preview: ['accent', 'Preview'],
};
export function statusBadge(status) {
  const [k, label] = STATUS[status] || ['', status];
  return h('span.badge', { class: k }, status === 'running' || status === 'uploading' ? h('span.dot.pulse') : null, label);
}

export function scorePill(v) {
  const cls = v >= 70 ? 'hi' : v >= 55 ? 'mid' : 'lo';
  return h('span.score-pill', { class: cls, title: 'Virality score (0–100)' }, Math.round(v ?? 0));
}

export function progressBar(pct, status) {
  return h('div.progress', { class: status }, h('span', { style: { width: Math.max(2, Math.min(100, pct || 0)) + '%' } }));
}

export function empty(iconName, title, text, action) {
  return h('div.empty', icon(iconName), h('h3', title), text ? h('p', text) : null, action || null);
}

export function card(title, sub, body, actions) {
  return h('div.card',
    title ? h('div.card-h', h('h2', title), sub ? h('span.sub', sub) : null, h('div', { style: { flex: 1 } }), actions || null) : null,
    h('div.card-b', body));
}

export function field(label, input, hint) {
  return h('div.field', h('label', label), input, hint ? h('div.hint', hint) : null);
}

export function clipCard(c) {
  const state = c.preview ? 'preview' : c.review_status;
  return h('a.clip-card', { href: `#/clips/${encodeURIComponent(c.id)}` },
    h('div.thumb', { style: c.file_exists ? { backgroundImage: `url(/media/thumb/${encodeURIComponent(c.id)}?v=${encodeURIComponent(c.updated_at || c.created_at || '')})` } : null },
      h('div.score', scorePill(c.virality)), h('div.state', statusBadge(state)), h('span.dur', fmt.dur(c.duration))),
    h('div.meta', h('div.t', c.title || c.id),
      h('div.row.small.muted', h('span', fmt.cat(c.category)),
        c.publications?.length ? h('span', '· ' + c.publications.map((p) => p.platform === 'youtube' ? 'YT' : 'IG').join(' ')) : null)));
}

// simple hover tooltip
const tip = () => document.getElementById('tooltip');
export function attachTooltip(el, build) {
  el.addEventListener('mousemove', (e) => {
    const t = tip(); clear(t); append(t, [build()]);
    t.style.opacity = '1';
    const x = Math.min(e.clientX + 14, window.innerWidth - t.offsetWidth - 8);
    t.style.left = x + 'px'; t.style.top = (e.clientY + 14) + 'px';
  });
  el.addEventListener('mouseleave', () => { tip().style.opacity = '0'; });
}

export function setTopbar(crumbs, actions = []) {
  const c = clear(document.getElementById('crumbs'));
  crumbs.forEach((cr, i) => {
    if (i) c.appendChild(h('span', '/'));
    c.appendChild(cr.href ? h('a', { href: cr.href }, cr.label) : h('h1', cr.label));
  });
  append(clear(document.getElementById('topbar-actions')), actions);
}

// chips input for tags/hashtags
export function chipsInput(values, { prefix = '', placeholder = 'Add…' } = {}) {
  let items = [...(values || [])];
  const wrap = h('div.chips');
  const input = h('input', { placeholder });
  const render = () => {
    clear(wrap);
    items.forEach((v, i) => wrap.appendChild(h('span.chip', v, h('button', { type: 'button', title: 'Remove', onclick: () => { items.splice(i, 1); render(); } }, '×'))));
    wrap.appendChild(input);
  };
  const add = () => {
    input.value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean).forEach((s) => {
      if (prefix && !s.startsWith(prefix)) s = prefix + s;
      s = prefix ? s.replace(/\s+/g, '') : s;
      if (!items.includes(s)) items.push(s);
    });
    input.value = ''; render(); input.focus();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
    else if (e.key === 'Backspace' && !input.value && items.length) { items.pop(); render(); }
  });
  input.addEventListener('blur', () => { if (input.value.trim()) add(); });
  wrap.addEventListener('click', (e) => { if (e.target === wrap) input.focus(); });
  render();
  return { el: wrap, get: () => { if (input.value.trim()) add(); return [...items]; } };
}

export function charCounter(input, max) {
  const c = h('div.counter');
  const upd = () => { c.textContent = `${input.value.length} / ${max}`; c.classList.toggle('over', input.value.length > max); };
  input.addEventListener('input', upd); upd();
  return c;
}
