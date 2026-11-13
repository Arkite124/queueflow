import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ErrorState, JobTable, Loading, PageHeading } from '../components/Common';
import { api, duration, time } from '../lib/api';
import type { Job, Page, Stats } from '../types';
import { useSession } from '../lib/session-context';
import ProcessingCharts from '../components/ProcessingCharts';

export default function Dashboard() {
  const { session } = useSession();
  const stats = useQuery({ queryKey: ['stats'], queryFn: () => api<Stats>('/api/v1/stats'), refetchInterval: 5000 });
  const recent = useQuery({ queryKey: ['jobs', 'recent'], queryFn: () => api<Page<Job>>('/api/v1/jobs?limit=6'), refetchInterval: 5000 });
  const s = stats.data;
  return <><PageHeading title="대시보드" action={<Link className="button" to="/new">＋ 새 작업</Link>} />
    <ErrorState error={stats.error} retry={() => { void stats.refetch(); }} />
    {stats.isPending && <Loading />}
    {s && <><div className="metrics">{[
      ['전체 작업', Object.values(s.counts).reduce((a, b) => a + b, 0), ''],
      ['처리 중', s.counts.RUNNING, `대기 ${s.counts.QUEUED} · 재시도 대기 ${s.counts.RETRY_WAIT}`],
      ['완료', s.counts.SUCCEEDED, `실패 ${s.counts.FAILED} · 취소 ${s.counts.CANCELLED}`],
      session?.user.is_admin ? ['실행 중인 워커', s.workers.filter(w => w.online).length, '응답 상태 기준'] : ['취소', s.counts.CANCELLED, ''],
    ].map(([label, value, note]) => <article className="metric" key={label}><p>{label}</p><strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong>{note && <small>{note}</small>}</article>)}</div>
    <section className="flow-strip"><div><span className={`signal ${s.draining ? 'amber' : ''}`} /><strong>{s.draining ? '새 작업 접수 중지' : '새 작업 접수 중'}</strong><span className="muted">업데이트 {time(s.observed_at)}</span></div><span className="muted">평균 처리 {duration(s.average_processing_ms)}</span></section></>}
    {s && <ProcessingCharts stats={s} jobs={recent.data?.items} />}
    <section className="panel"><div className="panel-heading"><h2>최근 작업</h2><Link to="/jobs">모든 작업 보기 →</Link></div><ErrorState error={recent.error} retry={() => { void recent.refetch(); }} />{recent.isPending ? <Loading /> : recent.data && <JobTable jobs={recent.data.items} />}</section>
  </>;
}
