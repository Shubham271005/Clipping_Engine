import { api } from '../api.js';
import { clear, clipCard, empty, h, setTopbar } from '../ui.js';

const FILTERS = [['pending', 'Needs review'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['', 'All']];

export async function render(page) {
  setTopbar([{ label: 'Clips' }]);
  let status = 'pending';
  const seg = h('div.seg');
  const prev = h('input', { type: 'checkbox' });
  const grid = h('div');
  page.append(h('div.row.wrap', { style: { marginBottom: '16px', justifyContent: 'space-between' } }, seg,
    h('label.check', prev, 'Show preview renders')), grid);
  prev.addEventListener('change', load);

  function drawSeg() {
    clear(seg).append(...FILTERS.map(([v, l]) => h('button', { class: status === v ? 'active' : '', onclick: () => { status = v; drawSeg(); load(); } }, l)));
  }

  async function load() {
    const q = new URLSearchParams({ include_previews: prev.checked });
    if (status) q.set('status', status);
    const clips = await api.get('/api/clips?' + q);
    clear(grid);
    if (!clips.length) {
      grid.appendChild(h('div.card', empty('film', status === 'pending' ? 'All caught up' : 'No clips here',
        'Clips appear after a clipping job renders them. Tick "Show preview renders" to see forced previews.',
        h('a.btn.primary', { href: '#/' }, 'New clipping job'))));
      return;
    }
    grid.appendChild(h('div.clip-grid', clips.map(clipCard)));
  }
  drawSeg();
  await load();
}
