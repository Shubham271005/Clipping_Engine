// Thin fetch wrapper around the Clip Studio backend.
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(method, url, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(url, opts); }
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
