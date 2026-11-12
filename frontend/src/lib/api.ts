const apiUrl = new URL(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8100', window.location.origin);
// Use the same loopback hostname in development so SameSite cookies work on either URL.
if (['localhost', '127.0.0.1'].includes(window.location.hostname) && ['localhost', '127.0.0.1'].includes(apiUrl.hostname)) apiUrl.hostname = window.location.hostname;
export const API = apiUrl.toString().replace(/\/$/, '');
let csrfToken: string | null = null;
export function setCsrfToken(value: string | null) { csrfToken = value; }
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (csrfToken && !['GET', 'HEAD'].includes(init?.method || 'GET')) headers.set('X-CSRF-Token', csrfToken);
  const response = await fetch(`${API}${path}`, { ...init, headers, credentials: 'include', signal: init?.signal ?? AbortSignal.timeout(30000) });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (response.status === 401 && path !== '/api/v1/auth/me') window.dispatchEvent(new Event('queueflow:session-expired'));
    throw new ApiError(body?.error?.message || `요청 실패 (HTTP ${response.status})`, response.status);
  }
  return response.json() as Promise<T>;
}

export async function readiness(): Promise<{ready: boolean; checks: Record<string, boolean>}> {
  const response = await fetch(`${API}/ready`, { credentials: 'include', signal: AbortSignal.timeout(15000) });
  if (response.status === 401) window.dispatchEvent(new Event('queueflow:session-expired'));
  if (response.status !== 200 && response.status !== 503) throw new Error(`준비 상태 요청 실패 (HTTP ${response.status})`);
  const result = await response.json();
  if (typeof result.ready !== 'boolean' || !result.checks) throw new Error('준비 상태를 확인할 수 없습니다.');
  return result;
}

export function bytes(n: number) { return n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MiB` : `${(n / 1024).toFixed(1)} KiB`; }
export function time(value: string | null) { return value ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—'; }
export function duration(ms: number | null) { return ms === null ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`; }
