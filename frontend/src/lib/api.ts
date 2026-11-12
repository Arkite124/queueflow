export const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8100').replace(/\/$/, '');

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, signal: init?.signal ?? AbortSignal.timeout(30000) });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message || `요청 실패 (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function readiness(): Promise<{ready: boolean; checks: Record<string, boolean>}> {
  const response = await fetch(`${API}/ready`, { signal: AbortSignal.timeout(15000) });
  if (response.status !== 200 && response.status !== 503) throw new Error(`준비 상태 요청 실패 (HTTP ${response.status})`);
  const result = await response.json();
  if (typeof result.ready !== 'boolean' || !result.checks) throw new Error('준비 상태를 확인할 수 없습니다.');
  return result;
}

export function bytes(n: number) { return n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MiB` : `${(n / 1024).toFixed(1)} KiB`; }
export function time(value: string | null) { return value ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—'; }
export function duration(ms: number | null) { return ms === null ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`; }
