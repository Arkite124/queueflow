import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSession } from '../lib/session-context';
import { ErrorState } from '../components/Common';

export default function Login() {
  const { signIn } = useSession();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const login = useMutation({ mutationFn: () => signIn(username.trim().toLowerCase(), password), onSettled: () => setPassword('') });
  return <div className="login-page">
    <div className="login-intro"><span className="brand-symbol">Q</span><h1>QueueFlow</h1><p>CSV 파일의 오류와 중복을 확인하고,<br />정리한 결과를 다운로드하세요.</p></div>
    <form className="panel padded login-card" onSubmit={e => { e.preventDefault(); login.mutate(); }}>
      <h2>로그인</h2>
      <p className="muted small">초대받은 계정으로 로그인하세요.</p>
      <label>사용자 이름<input name="username" autoComplete="username" maxLength={64} required value={username} disabled={login.isPending} onChange={e => setUsername(e.target.value)} /></label>
      <label>비밀번호<input name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} disabled={login.isPending} onChange={e => setPassword(e.target.value)} /></label>
      <ErrorState error={login.error} />
      <button className="button" type="submit" disabled={login.isPending}>{login.isPending ? '로그인 중…' : '로그인'}</button>
      <p className="muted small">계정이 필요하면 관리자에게 문의하세요.</p>
    </form>
  </div>;
}
