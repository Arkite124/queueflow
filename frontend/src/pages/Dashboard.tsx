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
  return <><PageHeading eyebrow="WORKSPACE / OVERVIEW" title="데이터 처리의 흐름을 한눈에" description="작업 등록부터 결과 확정까지, 실제 처리 상태를 확인합니다." action={<Link className="button" to="/new">＋ 새 작업</Link>} />
    <ErrorState error={stats.error} retry={() => { void stats.refetch(); }} />
    {stats.isPending && <Loading />}
    {s && <><div className="metrics">{[
      ['전체 작업', Object.values(s.counts).reduce((a, b) => a + b, 0), 'PostgreSQL에 등록된 작업'],
      ['처리 중', s.counts.RUNNING, `대기 ${s.counts.QUEUED} · 재시도 대기 ${s.counts.RETRY_WAIT}`],
      ['처리 완료', s.counts.SUCCEEDED, `실패 ${s.counts.FAILED} · 취소 ${s.counts.CANCELLED}`],
      session?.user.is_admin ? ['온라인 워커', s.workers.filter(w => w.online).length, '최근 heartbeat 기준'] : ['취소한 작업', s.counts.CANCELLED, '이 계정의 작업 기준'],
    ].map(([label, value, note]) => <article className="metric" key={label}><p>{label}</p><strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong><small>{note}</small></article>)}</div>
    <section className="flow-strip"><div><span className={`signal ${s.draining ? 'amber' : ''}`} /><strong>{s.draining ? '신규 작업 일시 중지' : '로컬 작업 공간'}</strong><span className="muted">최근 갱신 {time(s.observed_at)}</span></div><span className="mono muted">평균 처리 {duration(s.average_processing_ms)}</span></section></>}
    {s && <ProcessingCharts stats={s} jobs={recent.data?.items} />}
    <section className="panel"><div className="panel-heading"><h2>최근 작업</h2><Link to="/jobs">모든 작업 보기 →</Link></div><ErrorState error={recent.error} retry={() => { void recent.refetch(); }} />{recent.isPending ? <Loading /> : recent.data && <JobTable jobs={recent.data.items} />}</section>
    <div className="two-columns"><section className="panel padded"><p className="eyebrow">DURABLE BY DESIGN</p><h2>중단되어도 이어지는 작업</h2><p className="muted body-copy">등록된 작업과 재시도 일정은 데이터베이스에 보관합니다. 워커가 중단되면 만료된 실행 권한을 회수하고 새 실행을 예약합니다.</p>{session?.user.is_admin && <Link className="text-link" to="/system">시스템 상태 확인 ↗</Link>}</section><section className="panel padded"><p className="eyebrow">YOUR NEXT STEP</p><h2>CSV를 검증하고 정제하세요</h2><p className="muted body-copy">필수 값, 데이터 형식, 중복 기준을 설정하세요. 정제된 CSV와 행별 오류 보고서를 함께 받을 수 있습니다.</p><Link className="text-link" to="/new">작업 등록하기 ↗</Link></section></div>
  </>;
}
