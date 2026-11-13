import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ErrorState, Loading, PageHeading } from '../components/Common';
import { api, readiness, time } from '../lib/api';
import type { Stats } from '../types';
import { eventLabels } from '../lib/status';

const checkLabels: Record<string, string> = {
  database: '데이터베이스',
  redis: 'Redis',
  storage: '파일 저장소',
  accepting_jobs: '새 작업 접수',
};

export default function System() {
  const query = useQuery({ queryKey: ['stats'], queryFn: () => api<Stats>('/api/v1/stats'), refetchInterval: 5000 });
  const ready = useQuery({ queryKey: ['ready'], queryFn: readiness, refetchInterval: 15000 });
  const s = query.data;
  return <><PageHeading title="시스템 상태" />
    <ErrorState error={query.error} retry={() => { void query.refetch(); }} /><ErrorState error={ready.error} retry={() => { void ready.refetch(); }} />{query.isPending && <Loading />}
    {s && <><div className="two-columns"><section className="panel padded"><h2>Redis Streams</h2>{s.queue ? <dl className="system-grid"><div><dt>전달 대기</dt><dd>{s.queue.lag ?? '확인 불가'}</dd></div><div><dt>확인 대기 (Pending)</dt><dd>{s.queue.pending}</dd></div><div><dt>보관 중인 메시지</dt><dd>{s.queue.stream_length}</dd></div></dl> : <p className="muted">Redis 연결과 Consumer Group을 확인하세요.</p>}<p className="muted small">Pending은 워커의 처리 확인을 기다리는 메시지 수입니다.</p></section>
    <section className="panel padded"><h2>연결 상태</h2>{ready.data ? Object.entries(ready.data.checks).map(([name, ok]) => <div className="check-row" key={name}><span>{checkLabels[name] ?? name.replaceAll('_', ' ')}</span><span className={ok ? 'good' : 'bad'}>{ok ? '준비됨' : '확인 필요'}</span></div>) : <p className="muted">연결 상태를 불러오지 못했습니다.</p>}</section></div>
    <section className="panel"><div className="panel-heading"><h2>워커 연결</h2><span className="muted small">최근 1시간</span></div>{s.workers.length ? <div className="table-scroll"><table><thead><tr><th>워커</th><th>상태</th><th>마지막 응답</th><th>작업</th></tr></thead><tbody>{s.workers.map(w => <tr key={w.worker_id}><td className="mono">{w.worker_id}</td><td><span className={w.online ? 'good' : 'muted'}>● {w.online ? '연결됨' : '응답 지연'}</span></td><td>{time(w.seen_at)}</td><td>{w.current_job_id ? <Link className="mono" to={`/jobs/${w.current_job_id}`}>{w.current_job_id.slice(0, 8)} ↗</Link> : '—'}</td></tr>)}</tbody></table></div> : <div className="empty">연결된 워커가 없습니다. Worker 서비스를 확인하세요.</div>}</section>
    <section className="panel padded"><h2>복구·실패 기록</h2>{s.recoveries.length ? s.recoveries.map((e, i) => <div className="recovery-row" key={`${e.job_id}-${i}`}><strong>{eventLabels[e.event_type] ?? e.event_type}</strong><Link className="mono" to={`/jobs/${e.job_id}`}>{e.job_id.slice(0, 8)} ↗</Link><time className="muted">{time(e.created_at)}</time></div>) : <p className="muted">복구·실패 기록이 없습니다.</p>}</section></>}
  </>;
}
