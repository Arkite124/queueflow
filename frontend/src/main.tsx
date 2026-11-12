import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Dashboard from './pages/Dashboard';
import Jobs from './pages/Jobs';
import NewJob from './pages/NewJob';
import JobDetail from './pages/JobDetail';
import System from './pages/System';
import './styles.css';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchIntervalInBackground: false, refetchOnWindowFocus: true, staleTime: 1000 } } });
function App() {
  return <BrowserRouter><div className="app-shell"><aside className="sidebar"><Link to="/" className="brand"><span className="brand-symbol">Q</span><span>QueueFlow<small>PROCESSING CONSOLE</small></span></Link><div className="workspace-label"><span className="workspace-icon">L</span><div>Local workspace<small>단일 사용자 개발 환경</small></div></div><p className="nav-caption">WORKSPACE</p><nav aria-label="메인 메뉴">{[['/', '◫', '대시보드'], ['/jobs', '≡', '작업 목록'], ['/new', '＋', '새 작업']].map(([to, icon, label]) => <NavLink key={to} to={to} end={to === '/'}><span>{icon}</span>{label}</NavLink>)}<p className="nav-caption">OPERATIONS</p><NavLink to="/system"><span>◎</span>시스템 상태</NavLink></nav><div className="sidebar-footer"><span className="signal" /> LOCAL DEVELOPMENT<small>QueueFlow v0.1.0</small></div></aside><div className="main-shell"><div className="topbar"><span>QueueFlow <span className="muted">/ Workspace</span></span><span className="local-tag">LOCAL ONLY</span></div><main><Routes><Route path="/" element={<Dashboard />} /><Route path="/jobs" element={<Jobs />} /><Route path="/new" element={<NewJob />} /><Route path="/jobs/:id" element={<JobDetail />} /><Route path="/system" element={<System />} /><Route path="*" element={<div className="empty"><h1>페이지를 찾을 수 없습니다</h1><Link to="/">대시보드로 이동 →</Link></div>} /></Routes><footer className="content-footer"><span>QueueFlow · 비동기 데이터 처리 플랫폼</span><span>실제 작업 데이터 기반</span></footer></main></div></div></BrowserRouter>;
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}><App /></QueryClientProvider></React.StrictMode>);
