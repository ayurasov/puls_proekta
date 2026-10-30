/**
 * HTTP-клиент API: cookie-сессия, CSRF-заголовок, единый формат ошибок.
 * Ошибка сервера {error, message} превращается в ApiError с понятным текстом.
 */

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.status = status
    this.payload = payload
    this.code = payload?.error || null
  }
}

export class AuthRequiredError extends ApiError {}
export class NetworkError extends ApiError {}

let csrfToken = null
let onAuthLost = null

export function setAuthLostHandler(handler) {
  onAuthLost = handler
}

export async function ensureCsrf(force = false) {
  if (csrfToken && !force) return csrfToken
  const res = await fetch('/api/auth/csrf', { credentials: 'same-origin' })
  const data = await res.json()
  csrfToken = data.csrfToken
  return csrfToken
}

async function parse(res) {
  if (res.status === 204) return null
  const type = res.headers.get('Content-Type') || ''
  if (type.includes('application/json')) {
    try { return await res.json() } catch { return null }
  }
  return null
}

export async function request(path, { method = 'GET', body, params, form, signal, retryCsrf = true } = {}) {
  const url = new URL('/api' + path, window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value)
    }
  }
  const headers = {}
  if (method !== 'GET') {
    headers['X-CSRF-Token'] = await ensureCsrf()
  }
  let payload
  if (form) {
    payload = form
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(url.toString(), { method, headers, body: payload, credentials: 'same-origin', signal })
  } catch (err) {
    if (err?.name === 'AbortError') throw err
    throw new NetworkError('Нет связи с сервером', 0, null)
  }
  const data = await parse(res)
  if (res.ok) return data
  if (res.status === 400 && data?.error === 'csrf_error' && retryCsrf) {
    await ensureCsrf(true)
    return request(path, { method, body, params, form, signal, retryCsrf: false })
  }
  if (res.status === 401) {
    if (!path.startsWith('/auth/') && onAuthLost) onAuthLost()
    throw new AuthRequiredError(data?.message || 'Требуется вход в систему', 401, data)
  }
  if (res.status >= 500 && !data?.message) {
    throw new ApiError('Сервер временно недоступен — повторите попытку', res.status, data)
  }
  throw new ApiError(data?.message || `Ошибка ${res.status}`, res.status, data)
}

export const api = {
  get: (path, params, options = {}) => request(path, { method: 'GET', params, ...options }),
  post: (path, body) => request(path, { method: 'POST', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, form, method = 'PUT') => request(path, { method, form }),
}

/** Скачать файл с сервера (CSV реестра и т. п.) с текущей сессией. */
export async function downloadFrom(path, params, fallbackName) {
  const url = new URL('/api' + path, window.location.origin)
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value)
  }
  const res = await fetch(url.toString(), { credentials: 'same-origin' })
  if (!res.ok) {
    const data = await parse(res)
    throw new ApiError(data?.message || `Ошибка ${res.status}`, res.status, data)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const name = /filename="?([^";]+)"?/i.exec(disposition)?.[1] || fallbackName || 'download'
  saveBlob(await res.blob(), name)
}

export function saveBlob(blob, name) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(link.href), 60_000)
}
