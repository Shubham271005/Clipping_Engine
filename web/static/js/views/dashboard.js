import { api } from '../api.js';
import { card, clipCard, empty, fmt, h, icon, progressBar, setTopbar, statusBadge, toast } from '../ui.js';
import { newJobForm } from './newjob.js';

export async function render(page) {
  setTopbar([{ label: 'Overview' }]);
  const o = await api.get('/api/overview');

  const stat = (ic, label, value, foot, href) => h(href ? 'a.card.stat' : 'div.card.stat', { href },
    h('div.label', icon(ic), label), h('div.value', value), foot ? h('div.foot', foot) : null);

  const conn = o.connections;
  const connRow = (name, ic, c, detail) => h('div.row', { title: 'Connect with: python main.py auth youtube | instagram', style: { justifyContent: 'space-between', padding: '6px 0' } },
    h('span.row', icon(ic), name), c ? h('span.badge.good', detail || 'Connected') : h('span.badge', 'Not connected'));

  const autoToggle = h('input', { type: 'checkbox', checked: o.auto_publish });
  autoToggle.addEventListener('change', async () => {
    try {
      await api.put('/api/autopublish', { enabled: autoToggle.checked });
      toast(autoToggle.checked ? 'Auto-publish on: due clips upload every 5 minutes while this server runs.' : 'Auto-publish off', 'good');
    } catch (e) { autoToggle.checked = !autoToggle.checked; toast(e.message, 'bad'); }
  });

  page.append(
    h('div.grid.g4', { style: { marginBottom: '16px' } },
      stat('film', 'Needs review', fmt.n(o.clips.pending), `${o.clips.approved} approved · ${o.clips.rejected} rejected`, '#/clips'),
      stat('calendar', 'Scheduled', fmt.n(o.scheduled), 'upcoming uploads'),
      stat('rocket', 'Published', fmt.n(o.published), o.failed ? `${o.failed} failed or blocked` : 'all platforms'),
      stat('activity', 'Active jobs', fmt.n(o.jobs_active), `${o.sources} source video${o.sources === 1 ? '' : 's'}`, '#/jobs')),
    h('div.split',
      h('div.stack',
        card('New clipping job', 'Paste a stream, pick a profile', await newJobForm()),
        card('Review queue', 'Best pending clips first',
          o.review_queue.length ? h('div.clip-grid', o.review_queue.map(clipCard))
            : empty('film', 'Nothing to review', 'Rendered clips that pass the quality bar land here for your approval.'),
          h('a.btn.sm.ghost', { href: '#/clips' }, 'All clips'))),
      h('div.stack',
        card('Recent jobs', null,
          o.recent_jobs.length ? h('div.stack', o.recent_jobs.map((j) => h('a.stack', { href: `#/jobs/${j.id}`, style: { gap: '6px' } },
            h('div.row', h('span.ellipsis.small', { style: { flex: 1 } }, j.label), statusBadge(j.status)),
            ['running', 'paused', 'queued'].includes(j.status) ? h('div.row', h('div', { style: { flex: 1 } }, progressBar(j.progress, j.status)),
              h('span.small.muted', `${Math.round(j.progress)}% · ${j.stage || ''}`)) : h('span.small.muted', fmt.rel(j.created_at)))))
            : empty('activity', 'No jobs yet', 'Start one with the form on the left.'),
          h('a.btn.sm.ghost', { href: '#/jobs' }, 'All jobs')),
        card('Up next', 'Scheduled uploads',
          o.next_up.length ? h('div.stack', o.next_up.map((p) => {
            const t = JSON.parse(p.metadata_json || '{}').youtube?.best_title || p.clip_id;
            return h('a.row', { href: `#/clips/${encodeURIComponent(p.clip_id)}` }, icon(p.platform === 'youtube' ? 'youtube' : 'instagram'),
              h('span.ellipsis.small', { style: { flex: 1 } }, t), h('span.small.muted', fmt.date(p.scheduled_for)));
          })) : empty('calendar', 'Nothing scheduled', 'Approve clips from sources you have rights to and they are slotted automatically.')),
        card('Publishing', null, h('div.stack',
          connRow('YouTube', 'youtube', conn.youtube.connected, conn.youtube.channel?.title),
          connRow('Instagram', 'instagram', conn.instagram.connected, conn.instagram.username && '@' + conn.instagram.username),
          h('hr.sep', { style: { margin: '4px 0' } }),
          h('div.row', { style: { justifyContent: 'space-between' } },
            h('div', h('div', 'Auto-publish'), h('div.small.muted', 'Uploads due clips every 5 min while the dashboard runs')),
            h('label.toggle', autoToggle, h('span'))))))));
}
