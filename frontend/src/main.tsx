import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMutation } from '@tanstack/react-query';
import SessionProvider from './components/SessionProvider';
import { ErrorState } from './components/Common';
import { useSession } from './lib/session-context';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Jobs from './pages/Jobs';
import NewJob from './pages/NewJob';
import JobDetail from './pages/JobDetail';
import System from './pages/System';
import './styles.css';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchIntervalInBackground: false, refetchOnWindowFocus: true, staleTime: 1000 } } });
function App() {
  const { session, signOut } = useSession();
  const logout = useMutation({ mutationFn: signOut });
  if (!session) return <Login />;
  const local = session.mode === 'local';
  return <BrowserRouter><div className="app-shell"><aside className="sidebar"><Link to="/" className="brand"><span className="brand-symbol">Q</span><span>QueueFlow<small>PROCESSING CONSOLE</small></span></Link><div className="workspace-label"><span className="workspace-icon">{local ? 'L' : 'U'}</span><div>{local ? 'Local workspace' : session.user.username}<small>{local ? '단일 사용자 개발 환경' : '초대 계정 작업 공간'}</small></div></div><p className="nav-caption">WORKSPACE</p><nav aria-label="메인 메뉴">{[['/', '◫', '대시보드'], ['/jobs', '≡', '작업 목록'], ['/new', '＋', '새 작업']].map(([to, icon, label]) => <NavLink key={to} to={to} end={to === '/'}><span>{icon}</span>{label}</NavLink>)}{session.user.is_admin && <><p className="nav-caption">OPERATIONS</p><NavLink to="/system"><span>◎</span>시스템 상태</NavLink></>}</nav><div className="sidebar-footer"><span className="signal" /> {local ? 'LOCAL DEVELOPMENT' : 'INVITED DEMO'}<small>QueueFlow v0.1.0</small></div></aside><div className="main-shell"><div className="topbar"><span>QueueFlow <span className="muted">/ Workspace</span></span><div className="account-menu"><span className="local-tag">{local ? 'LOCAL ONLY' : session.user.username}</span>{!local && <button className="text-link" disabled={logout.isPending} onClick={() => logout.mutate()}>로그아웃</button>}</div></div><main><ErrorState error={logout.error} /><Routes><Route path="/" element={<Dashboard />} /><Route path="/jobs" element={<Jobs />} /><Route path="/new" element={<NewJob />} /><Route path="/jobs/:id" element={<JobDetail />} /><Route path="/system" element={session.user.is_admin ? <System /> : <div className="empty">관리자만 시스템 상태를 확인할 수 있습니다.</div>} /><Route path="*" element={<div className="empty"><h1>페이지를 찾을 수 없습니다</h1><Link to="/">대시보드로 이동 →</Link></div>} /></Routes><footer className="content-footer"><span>QueueFlow · 비동기 데이터 처리 플랫폼</span><span>실제 작업 데이터 기반</span></footer></main></div></div></BrowserRouter>;
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}><SessionProvider><App /></SessionProvider></QueryClientProvider></React.StrictMode>);
