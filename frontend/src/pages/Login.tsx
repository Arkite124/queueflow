import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSession } from '../lib/session-context';
import { ErrorState } from '../components/Common';

export default function Login() {
  const { signIn } = useSession();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const login = useMutation({ mutationFn: () => signIn(username.trim().toLowerCase(), password), onSettled: () => setPassword('') });
  return <div className="login-page"><div className="login-intro"><span className="brand-symbol">Q</span><p className="eyebrow">QUEUEFLOW / INVITED DEMO</p><h1>안전하게 이어지는<br />데이터 처리의 흐름</h1><p>초대된 계정으로 로그인해 CSV를 검증하고,<br />작업 상태와 처리 결과를 확인하세요.</p></div><form className="panel padded login-card" onSubmit={e => { e.preventDefault(); login.mutate(); }}><p className="eyebrow">WELCOME BACK</p><h2>초대 계정 로그인</h2><p className="muted small">관리자에게 전달받은 계정을 입력하세요. 공개 회원가입은 제공하지 않습니다.</p><label>사용자 이름<input name="username" autoComplete="username" maxLength={64} required value={username} disabled={login.isPending} onChange={e => setUsername(e.target.value)} /></label><label>비밀번호<input name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} disabled={login.isPending} onChange={e => setPassword(e.target.value)} /></label><ErrorState error={login.error} /><button className="button" type="submit" disabled={login.isPending}>{login.isPending ? '로그인 확인 중…' : '로그인 →'}</button><p className="muted small">업로드한 파일과 작업은 로그인한 계정에 연결됩니다. 테스트에는 합성 데이터를 사용하세요.</p></form></div>;
}
