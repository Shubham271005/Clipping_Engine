// Clip review & metadata editor (small version: watch, approve/reject, edit metadata, see AI reasoning).
import { api, apiUrl } from '../api.js';
import { busy, card, charCounter, chipsInput, clear, confirmDialog, field, fmt, h, icon, qcBadge, scorePill, setTopbar, statusBadge, toast } from '../ui.js';

const SCORE_KEYS = ['hook', 'entertainment', 'unexpectedness', 'emotion', 'relatability', 'story_clarity', 'replay', 'standalone',
  'audio_quality', 'visual_quality'];

export async function render(page, id) {
  const c = await api.get(`/api/clips/${encodeURIComponent(id)}`);
  setTopbar([{ label: 'Clips', href: '#/clips' }, { label: c.title || c.id }]);
  const m = c.metadata, yt = m.youtube || {}, ig = m.instagram || {};
  const state = c.preview ? 'preview' : c.review_status;

  // ---------------- left: player + review actions
  const video = h('video', { src: apiUrl(`/media/clip/${encodeURIComponent(c.id)}?v=${encodeURIComponent(c.updated_at || c.created_at || '')}`),
    controls: true, playsInline: true, preload: 'metadata' });
  const reviewBtns = h('div.grid.g2');
  const setReview = async (btn, status) => {
    const r = await busy(btn, () => api.post(`/api/clips/${encodeURIComponent(c.id)}/review`, { status }),
      status === 'approved' ? 'Approved' : status === 'rejected' ? 'Rejected' : 'Moved back to review');
    window.dispatchEvent(new Event('clipstudio:refresh-badges'));
    if (status === 'approved' && r.schedule && !r.schedule.scheduled && !c.source.publish_allowed) {
      // explain why nothing got scheduled
      setTimeout(() => import('../ui.js').then((u) => u.toast('Not scheduled: ' + c.source.publish_note, 'bad', 7000)), 300);
    }
    location.reload();
  };
  if (c.preview) {
    reviewBtns.append(h('div.callout.info', { style: { gridColumn: '1 / -1' } }, icon('info'),
      h('div', 'This is a preview of a moment the AI rejected. Previews are never published.')));
  } else if (c.locked) {
    reviewBtns.append(h('div.callout.good', { style: { gridColumn: '1 / -1' } }, icon('check'), h('div', 'Uploaded, so it can no longer be changed here.')));
  } else {
    reviewBtns.append(
      h('button.btn.good', { disabled: c.review_status === 'approved', onclick: (e) => setReview(e.currentTarget, 'approved') }, icon('check'), 'Approve'),
      h('button.btn.bad', { disabled: c.review_status === 'rejected', onclick: (e) => setReview(e.currentTarget, 'rejected') }, icon('x'), 'Reject'));
    if (c.review_status !== 'pending') reviewBtns.append(h('button.btn.ghost.sm', { style: { gridColumn: '1 / -1' },
      onclick: (e) => setReview(e.currentTarget, 'pending') }, 'Move back to review'));
  }

  // regenerate: re-run layout analysis + render + quality control for this clip
  const regen = h('button.btn', { disabled: c.locked || c.rerendering, style: { gridColumn: '1 / -1' } },
    icon('retry'), c.rerendering ? 'Regenerating…' : 'Regenerate clip');
  regen.addEventListener('click', async () => {
    if (!(await confirmDialog('Regenerate this clip?', 'The facecam/gameplay layout is re-detected, the clip is re-rendered and quality-checked again. Your title and description edits are kept.', 'Regenerate'))) return;
    const r = await busy(regen, () => api.post(`/api/clips/${encodeURIComponent(c.id)}/rerender`, {}));
    toast('Regenerating… this page refreshes when it is done', 'good');
    regen.disabled = true; regen.lastChild.textContent = 'Regenerating…';
    const t = setInterval(async () => {
      try {
        const j = await api.get(`/api/jobs/${r.id}`);
        if (['done', 'failed', 'cancelled'].includes(j.status)) {
          clearInterval(t);
          if (j.status === 'done') location.reload(); else toast(`Regeneration ${j.status}: ${j.stage}`, 'bad', 8000);
        }
      } catch { /* keep polling */ }
    }, 2500);
  });
  if (!c.preview) reviewBtns.append(regen);

  // quality control report
  const qc = c.qc;
  const qcCard = card('Quality control', qc ? `${qc.checks.filter((x) => x.status === 'pass').length}/${qc.checks.length} checks passed` : null,
    qc ? h('div.stack', { style: { gap: '6px' } },
      h('div.row', qcBadge(qc.status), qc.attempts?.length > 1 ? h('span.small.muted', `${qc.attempts.length} render attempts (auto-fixed)`) : null),
      ...qc.checks.map((x) => h('div.row', { style: { alignItems: 'flex-start', gap: '8px' } },
        h('span', { style: { color: x.status === 'pass' ? 'var(--good)' : x.status === 'warn' ? 'var(--warn)' : 'var(--bad)', marginTop: '1px' } },
          icon(x.status === 'pass' ? 'check' : x.status === 'warn' ? 'alert' : 'x')),
        h('div', { style: { minWidth: 0 } }, h('div.small', fmt.cat(x.name)), h('div.small.muted', x.detail)))))
      : h('div.callout.warn', icon('alert'), h('div', 'Rendered before automatic quality control existed. Regenerate to check it (approval needs a passing check).')));

  const src = c.source;
  const left = h('div.player.stack', video, reviewBtns, qcCard,
    card(null, null, h('dl.kv',
      h('dt', 'Status'), h('dd', statusBadge(state)),
      h('dt', 'Quality'), h('dd', qcBadge(c.qc_status)),
      h('dt', 'Score'), h('dd', scorePill(c.virality)),
      h('dt', 'Category'), h('dd', fmt.cat(c.category)),
      h('dt', 'Length'), h('dd', `${fmt.dur(c.duration)} · ${c.layout === 'facecam_stack' ? 'facecam + gameplay' : 'gameplay focus'}`),
      h('dt', 'From source'), h('dd', `${fmt.ts(c.source_start)} – ${fmt.ts(c.source_end)}`),
      h('dt', 'Streamer'), h('dd', src.streamer || '—'),
      h('dt', 'Rights'), h('dd', src.publish_allowed ? h('span.badge.good', icon('shield'), 'Cleared to publish')
        : h('span.badge.bad', { title: src.publish_note }, icon('shield'), 'Not cleared')),
      h('dt', 'Published'), h('dd', c.publications.length ? h('div.stack', { style: { gap: '4px' } }, c.publications.map((p) =>
        h('div.row', icon(p.platform === 'youtube' ? 'youtube' : 'instagram'), statusBadge(p.status),
          p.url ? h('a', { href: p.url, target: '_blank', rel: 'noopener' }, icon('external')) : h('span.small.muted', fmt.date(p.scheduled_for)))))
        : h('span.muted', 'Not yet')))));
  if (!src.publish_allowed && !c.preview) {
    left.append(h('div.callout.warn', icon('alert'), h('div', h('b', 'Publishing blocked. '), src.publish_note,
      '. Record the creator\'s written permission for this source before it can be scheduled.')));
  }

  // ---------------- right: metadata editor
  const title = h('input.input', { value: yt.best_title || '', maxLength: 100 });
  const altTitles = (yt.titles || []).filter((t) => t !== yt.best_title);
  const desc = h('textarea.textarea', { rows: 6, value: yt.description || '' });
  const tags = chipsInput(yt.tags, { placeholder: 'Add keyword, press Enter' });
  const ytTags = chipsInput(yt.hashtags, { prefix: '#', placeholder: '#hashtag' });
  const igCap = h('textarea.textarea', { rows: 5, value: ig.caption || '' });
  const igTags = chipsInput(ig.hashtags, { prefix: '#', placeholder: '#hashtag' });
  const igCta = h('input.input', { value: ig.cta || '' });
  const preview = h('div');

  function drawPreview(meta) {
    clear(preview).append(
      h('div.field', h('label', 'Final YouTube description'), h('div.log', { style: { height: 'auto', maxHeight: '220px' } }, meta.youtube.description_full)),
      h('div.field', h('label', 'Final Instagram caption'), h('div.log', { style: { height: 'auto', maxHeight: '220px' } }, meta.instagram.caption_full)));
  }
  drawPreview(m);

  const save = h('button.btn.primary', { disabled: c.locked }, icon('check'), 'Save metadata');
  save.addEventListener('click', async () => {
    const body = {
      youtube: { best_title: title.value, description: desc.value, tags: tags.get(), hashtags: ytTags.get(),
        titles: [title.value, ...altTitles.filter((t) => t !== title.value)] },
      instagram: { caption: igCap.value, hashtags: igTags.get(), cta: igCta.value },
    };
    const meta = await busy(save, () => api.put(`/api/clips/${encodeURIComponent(c.id)}/metadata`, body), 'Metadata saved');
    drawPreview(meta);
  });

  const scores = c.moment.scores || {};
  const right = h('div.stack',
    card('YouTube Shorts', null, h('div.stack',
      field('Title', h('div.stack', { style: { gap: '4px' } }, title, charCounter(title, 100))),
      altTitles.length ? h('div.row.wrap.small', h('span.muted', 'Alternatives:'), altTitles.map((t) =>
        h('button.btn.sm.wrap-text', { type: 'button', onclick: () => { title.value = t; title.dispatchEvent(new Event('input')); } }, t))) : null,
      field('Description', desc, 'Use {CREDIT} to place the creator credit. It is added automatically if missing.'),
      h('div.grid.g2', field('Hashtags', ytTags.el), field('Search tags', tags.el)))),
    card('Instagram Reels', null, h('div.stack',
      field('Caption', igCap), h('div.grid.g2', field('Hashtags', igTags.el), field('Call to action', igCta)))),
    h('div.row', save, h('span.small.muted', c.locked ? 'Locked after upload' : 'Validated for YouTube and Instagram limits on save')),
    card('Final text that gets posted', null, preview),
    card('Why the AI picked this', c.moment.reject_reason ? 'AI rejected it: ' + c.moment.reject_reason : null, h('div.stack',
      c.moment.summary ? h('div', h('div.small.muted', 'What happens'), h('div', c.moment.summary)) : null,
      c.moment.why ? h('div', h('div.small.muted', 'Why it works'), h('div', c.moment.why)) : null,
      m.on_screen_hook ? h('div', h('div.small.muted', 'On-screen hook'), h('div', m.on_screen_hook)) : null,
      Object.keys(scores).length ? h('div.scorebars', SCORE_KEYS.filter((k) => k in scores).map((k) => [
        h('span.dim', fmt.cat(k)), h('div.bar', h('span', { style: { width: (scores[k] * 10) + '%' } })), h('span.num.small', (+scores[k]).toFixed(1))])) : null,
      c.transcript ? h('div', h('div.small.muted', 'Transcript'), h('div.small.dim', c.transcript)) : null)),
    m.commentary?.script ? card('Original commentary script', 'Record this in your own voice to make the clip more original',
      h('div.stack', m.commentary.intro_line ? h('div', h('div.small.muted', 'Intro line'), h('div', m.commentary.intro_line)) : null,
        h('div', h('div.small.muted', 'Script'), h('div', m.commentary.script)),
        m.commentary.notes ? h('div.small.muted', m.commentary.notes) : null,
        h('div.small.muted', h('code', `python main.py narrate ${c.id} my_voice.wav`), ' mixes it in.'))) : null);

  page.append(h('div.editor', left, right));
}
