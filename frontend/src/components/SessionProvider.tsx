import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, setCsrfToken } from '../lib/api';
import { SessionContext } from '../lib/session-context';
import type { Session } from '../lib/session-context';
import { ErrorState, Loading } from './Common';

export default function SessionProvider({ children }: { children: React.ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['session'], queryFn: async () => {
    try { const session = await api<Session>('/api/v1/auth/me'); setCsrfToken(session.csrf_token); return session; }
    catch (error) { if (error instanceof ApiError && error.status === 401) { setCsrfToken(null); return null; } throw error; }
  }, refetchInterval: 60000, retry: 1 });
  useEffect(() => {
    function expired() {
      setCsrfToken(null);
      void client.cancelQueries({ predicate: q => q.queryKey[0] !== 'session' });
      client.removeQueries({ predicate: q => q.queryKey[0] !== 'session' });
      client.setQueryData(['session'], null);
    }
    window.addEventListener('queueflow:session-expired', expired);
    return () => window.removeEventListener('queueflow:session-expired', expired);
  }, [client]);
  async function update(session: Session | null) {
    await client.cancelQueries({ predicate: q => q.queryKey[0] !== 'session' });
    client.removeQueries({ predicate: q => q.queryKey[0] !== 'session' });
    setCsrfToken(session?.csrf_token ?? null);
    client.setQueryData(['session'], session);
  }
  async function signIn(username: string, password: string) {
    const session = await api<Session>('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
    setCsrfToken(session.csrf_token);
    // Verify the browser actually retained the HttpOnly cookie before opening protected pages.
    await api<Session>('/api/v1/auth/me');
    await update(session);
  }
  async function signOut() {
    await api('/api/v1/auth/logout', { method: 'POST' });
    await update(null);
  }
  if (query.isPending) return <div className="session-surface"><Loading /></div>;
  if (query.error) return <div className="session-surface"><ErrorState error={query.error} retry={() => { void query.refetch(); }} /></div>;
  return <SessionContext.Provider value={{ session: query.data ?? null, signIn, signOut }}>{children}</SessionContext.Provider>;
}
