// Hash router + global chrome (nav badges, session).
import { api } from './api.js';
import { clear, h, hydrateIcons, icon, toast } from './ui.js';

const ROUTES = [
  [/^$/, () => import('./views/dashboard.js')],
  [/^jobs$/, () => import('./views/jobs.js')],
  [/^jobs\/(\d+)$/, () => import('./views/job.js')],
  [/^clips$/, () => import('./views/clips.js')],
  [/^clips\/(.+)$/, () => import('./views/clip.js')],
];

let cleanup = null;
let navToken = 0;

async function route() {
  const path = location.hash.replace(/^#\/?/, '').split('?')[0];
  const token = ++navToken;
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  document.getElementById('sidebar').classList.remove('open');
  const top = path.split('/')[0];
  document.querySelectorAll('#nav a').forEach((a) => {
    const r = a.dataset.route;
    a.classList.toggle('active', r === top || (r === 'library' && top === 'sources') || (r === 'profiles' && top === 'profiles'));
  });
  const view = clear(document.getElementById('view'));
  const page = h('div.page');
  view.appendChild(page);
  for (const [rx, loader] of ROUTES) {
    const m = path.match(rx);
    if (!m) continue;
    try {
      const mod = await loader();
      if (token !== navToken) return;
      cleanup = await mod.render(page, ...m.slice(1).map(decodeURIComponent));
    } catch (e) {
      if (token !== navToken) return;
      clear(page).appendChild(h('div.callout.bad', icon('alert'), h('div', h('b', 'Could not load this page. '), e.message)));
      console.error(e);
    }
    return;
  }
  page.appendChild(h('div.empty', h('h3', 'Page not found'), h('a.btn', { href: '#/' }, 'Go to overview')));
}

async function refreshBadges() {
  try {
    const o = await api.get('/api/overview');
    const set = (id, n) => { const el = document.getElementById(id); el.hidden = !n; el.textContent = n; };
    set('nav-jobs', o.jobs_active);
    set('nav-review', o.clips.pending);
  } catch { /* server down: api() already toasts on user actions */ }
}

async function initChrome() {
  hydrateIcons();
  document.getElementById('menu-btn').addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));
  const foot = document.getElementById('sidebar-foot');
  try {
    const s = await api.get('/api/session');
    if (s.auth_required) {
      foot.appendChild(h('button.btn.ghost.sm', { onclick: async () => { await api.post('/api/logout'); location.href = '/login.html'; } },
        icon('logout'), 'Sign out'));
    } else foot.appendChild(h('span', { title: 'Server bound to localhost, no login required' }, '● Local mode'));
  } catch (e) { toast(e.message, 'bad'); }
}

window.addEventListener('hashchange', route);
window.addEventListener('clipstudio:refresh-badges', refreshBadges);
initChrome();
route();
refreshBadges();
setInterval(refreshBadges, 8000);
