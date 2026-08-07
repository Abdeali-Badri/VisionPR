const apiBaseUrl = (import.meta.env.VITE_API_URL || '').trim().replace(/\/+$/, '')

export const apiUrl = (path: string): string => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${apiBaseUrl}${normalizedPath}`
}

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const response = await fetch(apiUrl(path), {
    credentials: 'include',
    ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: 'The request failed.' }))
    throw new Error(payload.detail || 'The request failed.')
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form })
  },
}
