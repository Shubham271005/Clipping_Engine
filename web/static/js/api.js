// Thin fetch wrapper around the Clip Studio backend.
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

// Separate-backend mode (e.g. the Vercel-hosted frontend): only when the page is NOT served by the
// backend itself. Opened from http://127.0.0.1:8765 or through the backend's own URL, calls stay same-origin,
// so the local dashboard keeps working when the tunnel URL changes or the tunnel is down.
const _configured = String(window.CLIP_STUDIO_API_URL || '').replace(/\/$/, '');
const _servedByBackend = ['localhost', '127.0.0.1', '::1'].includes(location.hostname)
  || (_configured && new URL(_configured).origin === location.origin);
export const API_BASE = _servedByBackend ? '' : _configured;

export function apiUrl(url) {
  if (/^https?:\/\//i.test(url)) return url;
  return API_BASE ? `${API_BASE}${url.startsWith('/') ? url : `/${url}`}` : url;
}

async function request(method, url, body, isForm = false) {
  const opts = { method, headers: {}, credentials: 'include' };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(apiUrl(url), opts); }
  catch { throw new ApiError('Cannot reach the Clip Studio server. Is it running?', 0); }
  if (res.status === 401) { location.href = '/login.html'; throw new ApiError('Sign in required', 401); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && (data.detail?.toString?.() || data.detail)) || res.statusText;
    throw new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), res.status);
  }
  return data;
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b = {}) => request('POST', u, b),
  put: (u, b = {}) => request('PUT', u, b),
  del: (u) => request('DELETE', u),
  upload: (u, formData) => request('POST', u, formData, true),
};
