import { createContext, useContext } from 'react';

export interface Session { mode: 'local' | 'session'; user: { username: string; is_admin: boolean }; csrf_token: string | null; public_registration: boolean }
export interface SessionContextValue { session: Session | null; signIn: (username: string, password: string) => Promise<void>; signOut: () => Promise<void> }
export const SessionContext = createContext<SessionContextValue | null>(null);
export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error('Session provider is missing');
  return value;
}
