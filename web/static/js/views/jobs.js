import { api } from '../api.js';
import { clear, empty, fmt, h, icon, modal, progressBar, setTopbar, statusBadge } from '../ui.js';
import { newJobForm } from './newjob.js';

export const KIND = { process: 'Clip stream', rerender: 'Re-render', render_candidate: 'Render moment', publish: 'Publish',
  analytics: 'Analytics', test_upload: 'Test upload' };

export async function render(page) {
  setTopbar([{ label: 'Jobs' }], [h('button.btn.primary.sm', { onclick: openNew }, icon('plus'), 'New job')]);
  const body = h('div');
  page.appendChild(h('div.card', body));

  async function load() {
    const jobs = await api.get('/api/jobs?limit=100');
    clear(body);
    if (!jobs.length) {
      body.appendChild(empty('activity', 'No jobs yet', 'Clipping, re-render, publishing and analytics runs show up here with live logs.',
        h('button.btn.primary', { onclick: openNew }, icon('plus'), 'New clipping job')));
      return;
    }
    body.appendChild(h('div.table-wrap', h('table.table',
      h('thead', h('tr', h('th', '#'), h('th', 'Job'), h('th', 'Type'), h('th', 'Status'), h('th', { style: { width: '220px' } }, 'Progress'), h('th', 'Created'))),
      h('tbody', jobs.map((j) => {
        const pct = j.status === 'done' ? 100 : j.progress;
        return h('tr.clickable', { onclick: () => { location.hash = `#/jobs/${j.id}`; } },
          h('td.muted', j.id), h('td', h('div.ellipsis', { style: { maxWidth: '420px' } }, j.label)), h('td.dim', KIND[j.kind] || j.kind),
          h('td', statusBadge(j.status)),
          h('td', h('div.row', h('div', { style: { flex: 1 } }, progressBar(pct, j.status)),
            h('span.small.muted', { style: { width: '36px', textAlign: 'right' } }, `${Math.round(pct)}%`))),
          h('td.small.muted', fmt.rel(j.created_at)));
      })))));
  }

  async function openNew() {
    await modal({ title: 'New clipping job', body: await newJobForm({ compact: true, onCreated: (id) => {
      document.querySelector('.modal-bg')?.remove(); location.hash = `#/jobs/${id}`;
    } }) });
  }

  await load();
  const t = setInterval(() => load().catch(() => {}), 2500);
  return () => clearInterval(t);
}
