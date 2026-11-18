// "New clipping job" form - shared by the overview page and the jobs page.
import { api } from '../api.js';
import { busy, field, h, icon, toast } from '../ui.js';

export async function newJobForm({ compact = false, onCreated } = {}) {
  const profiles = await api.get('/api/profiles');
  const url = h('input.input', { placeholder: 'https://www.twitch.tv/videos/…  or  C:\\path\\to\\vod.mp4', required: true });
  const profile = h('select.select', profiles.filter((p) => !p.error).map((p) => h('option', { value: p.id }, `${p.name}`)));
  profile.value = profiles.find((p) => p.id === 'gta6') ? 'gta6' : profiles[0]?.id;
  const streamer = h('input.input', { placeholder: 'Creator name (credited in every post)' });
  const game = h('input.input', { placeholder: 'Optional, e.g. "GTA 6 trailer (reaction)"' });
  const rights = h('select.select',
    h('option', { value: '' }, 'Unknown: render only, never publish'),
    h('option', { value: 'owned' }, 'I own this content'),
    h('option', { value: 'permission_granted' }, 'Creator gave me written permission'),
  );
  const commercial = h('input', { type: 'checkbox' });
  const authRef = h('input.input', { placeholder: 'Where the permission lives: email, contract, DM link…' });
  const authOn = h('input.input', { type: 'date' });
  const restrictions = h('input.input', { placeholder: 'e.g. no-instagram' });
  const maxClips = h('input.input', { type: 'number', min: 1, max: 60, placeholder: 'auto (up to 40)' });
  const minScore = h('input.input', { type: 'number', min: 0, max: 100, placeholder: 'default 60' });
  const forceTop = h('input.input', { type: 'number', min: 0, max: 20, placeholder: '0' });
  const noLlm = h('input', { type: 'checkbox' });

  const rightsBox = h('div.stack', { hidden: true },
    h('div.grid.g2', field('Permission reference', authRef, 'Required for creator permission'), field('Granted on', authOn)),
    h('label.check', commercial, 'Includes explicit commercial-use permission (needed to publish)'),
    field('Restrictions', restrictions, 'no-youtube · no-instagram · no-publish'));
  rights.addEventListener('change', () => { rightsBox.hidden = !rights.value; });

  const advanced = h('details', { style: { marginTop: '4px' } },
    h('summary.small.dim', { style: { cursor: 'pointer', userSelect: 'none' } }, 'Advanced options'),
    h('div.stack', { style: { marginTop: '12px' } },
      h('div.grid.g3', field('Max clips', maxClips), field('Min. virality', minScore),
        field('Preview rejected moments', forceTop, 'Renders the N best AI rejects as previews')),
      h('label.check', noLlm, 'Signal-only mode (no AI review; weaker results)')));

  const submit = h('button.btn.primary', { type: 'submit' }, icon('sparkles'), 'Start clipping');
  const form = h('form.stack', {
    onsubmit: async (e) => {
      e.preventDefault();
      if (!url.value.trim()) return toast('Paste a VOD URL or a local file path', 'bad');
      const body = {
        url: url.value.trim(), profile: profile.value, streamer: streamer.value.trim() || null, game: game.value.trim() || null,
        rights: rights.value || null, commercial: commercial.checked, auth_ref: authRef.value.trim() || null,
        authorized_on: authOn.value || null, restrictions: restrictions.value.trim() || null,
        max_clips: maxClips.value ? +maxClips.value : null, min_score: minScore.value ? +minScore.value : null,
        force_top: forceTop.value ? +forceTop.value : 0, no_llm: noLlm.checked,
      };
      const r = await busy(submit, () => api.post('/api/jobs', body), 'Job queued');
      window.dispatchEvent(new Event('clipstudio:refresh-badges'));
      onCreated ? onCreated(r.id) : (location.hash = `#/jobs/${r.id}`);
    },
  },
    field('Livestream / VOD', url),
    h('div.grid', { class: compact ? 'g2' : 'g3' }, field('Content profile', profile), field('Streamer', streamer), compact ? null : field('Game label', game)),
    compact ? field('Game label', game) : null,
    field('Rights', rights), rightsBox, advanced,
    h('div.row', submit, h('span.small.muted', 'Runs on this PC: GPU transcription → AI review → render.')));
  return form;
}
