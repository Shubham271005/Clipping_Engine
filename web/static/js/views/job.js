import { api } from '../api.js';
import { busy, clear, confirmDialog, fmt, h, icon, progressBar, setTopbar, statusBadge } from '../ui.js';
import { KIND } from './jobs.js';

const LIVE = ['queued', 'running', 'paused'];

export async function render(page, id) {
  const head = h('div.card.card-b.stack');
  const actions = h('div.row.wrap');
  const logBox = h('div.log');
  const result = h('div');
  let offset = 0, autoscroll = true, timer;
  logBox.addEventListener('scroll', () => { autoscroll = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 30; });

  page.append(head, h('div', { style: { height: '16px' } }), result,
    h('div.card', h('div.card-h', h('h2', 'Live log'), h('span.sub', 'stdout of the engine process'), h('div', { style: { flex: 1 } }), actions),
      h('div.card-b', logBox)));

  function colorize(text) {
    const frag = document.createDocumentFragment();
    for (const line of text.split(/(?<=\n)/)) {
      const cls = /ERROR|Traceback|Error:/.test(line) ? 'e' : /WARNING/.test(line) ? 'w' : /Rendered|Review: |done ->|OK/.test(line) ? 'ok' : / INFO /.test(line) ? 'i' : '';
      frag.appendChild(h('span', { class: cls }, line));
    }
    return frag;
  }

  async function act(btn, action, confirmText) {
    if (confirmText && !(await confirmDialog('Are you sure?', confirmText, 'Yes', 'bad'))) return;
    const r = await busy(btn, () => api.post(`/api/jobs/${id}/${action}`), `Job ${action === 'retry' ? 'requeued' : action + 'd'}`);
    if (action === 'retry' && r?.id) location.hash = `#/jobs/${r.id}`;
    else tick();
  }

  async function tick() {
    const j = await api.get(`/api/jobs/${id}`);
    setTopbar([{ label: 'Jobs', href: '#/jobs' }, { label: `#${j.id}` }]);
    const pct = j.status === 'done' ? 100 : j.progress;
    clear(head).append(
      h('div.row', h('div', { style: { flex: 1, minWidth: 0 } },
        h('div.row', h('h2', { style: { margin: 0, fontSize: '16px' } }, KIND[j.kind] || j.kind), statusBadge(j.status)),
        h('div.small.muted.ellipsis', j.label))),
      h('div.row', h('div', { style: { flex: 1 } }, progressBar(pct, j.status)), h('b', `${Math.round(pct)}%`)),
      h('div.row.small.muted.wrap', h('span', j.stage || '—'), h('span', '·'), h('span', `Created ${fmt.rel(j.created_at)}`),
        j.started_at ? h('span', `· started ${fmt.date(j.started_at)}`) : null,
        j.finished_at ? h('span', `· finished ${fmt.date(j.finished_at)}`) : null,
        j.source_id ? h('span', `· source ${j.source_id}`) : null));
    clear(actions);
    if (j.status === 'running') actions.append(h('button.btn.sm', { onclick: (e) => act(e.currentTarget, 'pause') }, icon('pause'), 'Pause'));
    if (j.status === 'paused') actions.append(h('button.btn.sm', { onclick: (e) => act(e.currentTarget, 'resume') }, icon('resume'), 'Resume'));
    if (LIVE.includes(j.status)) actions.append(h('button.btn.sm.bad', { onclick: (e) => act(e.currentTarget, 'cancel', 'This stops the job and its ffmpeg/whisper processes. Partial outputs are kept.') }, icon('stop'), 'Cancel'));
    if (['failed', 'cancelled', 'interrupted'].includes(j.status)) actions.append(h('button.btn.sm', { onclick: (e) => act(e.currentTarget, 'retry') }, icon('retry'), 'Retry'));

    const lg = await api.get(`/api/jobs/${id}/log?offset=${offset}`);
    if (lg.text) {
      if (offset === 0) clear(logBox);
      logBox.appendChild(colorize(lg.text));
      offset = lg.offset;
      if (autoscroll) logBox.scrollTop = logBox.scrollHeight;
    } else if (offset === 0) {
      logBox.textContent = j.status === 'queued' ? 'Waiting in queue… (one heavy job runs at a time)' : 'No output yet.';
    }

    clear(result);
    if (j.result && j.kind === 'process') {
      const r = j.result;
      result.append(h('div.card.card-b', { style: { marginBottom: '16px' } }, h('div.row.wrap', { style: { gap: '18px' } },
        h('div', h('div.small.muted', 'Candidates'), h('b', r.candidates)), h('div', h('div.small.muted', 'Selected'), h('b', r.selected)),
        h('div', h('div.small.muted', 'Rendered'), h('b', r.rendered)), h('div', h('div.small.muted', 'Scheduled'), h('b', r.scheduled)),
        h('div', { style: { flex: 1 } }),
        r.publishing_allowed ? null : h('span.badge.warn', icon('shield'), 'Publishing blocked: ' + r.publishing_note),
        h('a.btn.primary.sm', { href: '#/clips' }, 'Review clips'))));
    } else if (j.result && j.result.clip_id) {
      result.append(h('div.card.card-b.row', { style: { marginBottom: '16px' } }, icon('check'), h('span', { style: { flex: 1 } }, 'Clip ready'),
        h('a.btn.primary.sm', { href: `#/clips/${encodeURIComponent(j.result.clip_id)}` }, 'Open clip')));
    }
    if (!LIVE.includes(j.status) && timer) { clearInterval(timer); timer = null; window.dispatchEvent(new Event('clipstudio:refresh-badges')); }
  }

  await tick();
  timer = setInterval(() => tick().catch(() => {}), 1500);
  return () => timer && clearInterval(timer);
}
