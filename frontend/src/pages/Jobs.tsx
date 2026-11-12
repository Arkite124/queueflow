import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ErrorState, JobTable, Loading, PageHeading } from '../components/Common';
import { api } from '../lib/api';
import type { Job, Page } from '../types';

export default function Jobs() {
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ['jobs', status, search, offset], queryFn: () => api<Page<Job>>(`/api/v1/jobs?limit=20&offset=${offset}&q=${encodeURIComponent(search)}${status ? `&status=${status}` : ''}`), refetchInterval: 5000 });
  return <><PageHeading eyebrow="WORKSPACE / JOBS" title="작업 목록" description="브라우저를 다시 열어도 저장된 작업과 결과를 확인할 수 있습니다." action={<Link className="button" to="/new">＋ 새 작업</Link>} />
    <section className="panel"><div className="filters"><label>파일 검색<input type="search" placeholder="파일명으로 검색" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label><label>작업 상태<select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">모든 상태</option>{['QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'CANCELLED'].map(s => <option key={s}>{s}</option>)}</select></label><span className="muted">{query.data?.total ?? '—'}개 작업</span></div>
    <ErrorState error={query.error} retry={() => { void query.refetch(); }} />{query.isPending ? <Loading /> : query.data && <JobTable jobs={query.data.items} />}
    <div className="pagination"><button className="button secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>이전</button><span>{Math.floor(offset / 20) + 1} 페이지</span><button className="button secondary" disabled={!query.data || offset + 20 >= query.data.total} onClick={() => setOffset(offset + 20)}>다음</button></div></section></>;
}
