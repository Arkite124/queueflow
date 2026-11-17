import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useSession } from '../lib/session-context';
import { ErrorState } from '../components/Common';
import { api, ApiError } from '../lib/api';

async function authConfig() {
  try { return await api<{ public_registration: boolean; max_accounts_per_ip: number }>('/api/v1/auth/config'); }
  catch (error) {
    // Pages can deploy before the server. Older APIs keep their existing login
    // flow until the signup endpoints are deployed.
    if (error instanceof ApiError && error.status === 404) return { public_registration: false, max_accounts_per_ip: 3 };
    throw error;
  }
}

export default function Login() {
  const { signIn } = useSession();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [registering, setRegistering] = useState(false);
  const [notice, setNotice] = useState('');
  const config = useQuery({ queryKey: ['auth-config'], queryFn: authConfig, staleTime: 60000 });
  const login = useMutation({ mutationFn: () => signIn(username.trim().toLowerCase(), password), onSettled: () => setPassword('') });
  const registration = useMutation({
    mutationFn: () => api('/api/v1/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: username.trim().toLowerCase(), password }) }),
    onSuccess: () => { setRegistering(false); setNotice('가입했습니다. 아이디와 비밀번호로 로그인하세요.'); },
    onSettled: () => setPassword(''),
  });
  const busy = login.isPending || registration.isPending;
  function changeMode() {
    setRegistering(!registering);
    setPassword('');
    setNotice('');
    login.reset();
    registration.reset();
  }
  return <div className="login-page">
    <div className="login-intro"><span className="brand-symbol">Q</span><h1>QueueFlow</h1><p>CSV 파일의 오류와 중복을 확인하고,<br />정리한 결과를 다운로드하세요.</p></div>
    <form className="panel padded login-card" onSubmit={e => { e.preventDefault(); setNotice(''); if (registering) registration.mutate(); else login.mutate(); }}>
      <h2>{registering ? '회원가입' : '로그인'}</h2>
      <p className="muted small">{registering ? '아이디와 비밀번호만 입력하면 됩니다.' : '내 계정으로 로그인하세요.'}</p>
      {notice && <p className="notice" role="status">{notice}</p>}
      <label>아이디<input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} minLength={3} maxLength={64} pattern={registering ? '[A-Za-z0-9][A-Za-z0-9_.\\-]{2,63}' : undefined} aria-describedby={registering ? 'username-help' : undefined} required value={username} disabled={busy} onChange={e => setUsername(e.target.value)} />
        {registering && <small id="username-help" className="muted auth-help">영문·숫자로 시작하는 3~64자. 점, 밑줄, 하이픈도 사용할 수 있습니다. 대소문자는 구분하지 않습니다.</small>}
      </label>
      <label>비밀번호<input name="password" type="password" autoComplete={registering ? 'new-password' : 'current-password'} minLength={registering ? 12 : undefined} maxLength={128} aria-describedby={registering ? 'password-help' : undefined} required value={password} disabled={busy} onChange={e => setPassword(e.target.value)} />
        {registering && <small id="password-help" className="muted auth-help">12~128자로 입력하세요.</small>}
      </label>
      <ErrorState error={registering ? registration.error : login.error} />
      <button className="button" type="submit" disabled={busy}>{busy ? (registering ? '가입 중…' : '로그인 중…') : (registering ? '가입하기' : '로그인')}</button>
      {(config.data?.public_registration || registering) && <p className="auth-switch">{registering ? '이미 계정이 있나요?' : '처음 방문했나요?'} <button className="text-link" type="button" disabled={busy} onClick={changeMode}>{registering ? '로그인' : '회원가입'}</button></p>}
      {config.isError && <p className="muted small">회원가입 정보를 불러오지 못했습니다. <button className="text-link" type="button" onClick={() => { void config.refetch(); }}>다시 시도</button></p>}
      {registering && <p className="muted small">중복 가입을 막기 위해 가입 네트워크를 기록합니다. 같은 IP에서는 최대 {config.data?.max_accounts_per_ip ?? 3}개 계정을 만들 수 있습니다.</p>}
      <p className="muted small">비밀번호 찾기는 지원하지 않습니다.{registering && ' 새 계정을 만들어도 이전 계정의 작업은 볼 수 없습니다.'}</p>
    </form>
  </div>;
}
