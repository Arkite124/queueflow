import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ErrorState, Loading, PageHeading } from '../components/Common';
import { api, readiness, time } from '../lib/api';
import type { Stats } from '../types';

export default function System() {
  const query = useQuery({ queryKey: ['stats'], queryFn: () => api<Stats>('/api/v1/stats'), refetchInterval: 5000 });
  const ready = useQuery({ queryKey: ['ready'], queryFn: readiness, refetchInterval: 15000 });
  const s = query.data;
  return <><PageHeading eyebrow="OPERATIONS / SYSTEM" title="시스템 상태" description="로컬 관리자 범위 · 데이터베이스와 실제 heartbeat를 기준으로 표시합니다." />
    <ErrorState error={query.error} retry={() => { void query.refetch(); }} /><ErrorState error={ready.error} retry={() => { void ready.refetch(); }} />{query.isPending && <Loading />}
    {s && <><div className="two-columns"><section className="panel padded"><p className="eyebrow">DELIVERY</p><h2>Redis Streams</h2>{s.queue ? <dl className="system-grid"><div><dt>아직 전달되지 않은 메시지</dt><dd>{s.queue.lag ?? '확인 불가'}</dd></div><div><dt>Pending 메시지</dt><dd>{s.queue.pending}</dd></div><div><dt>보관 중인 전체 메시지</dt><dd>{s.queue.stream_length}</dd></div></dl> : <p className="muted">큐 상태를 확인할 수 없습니다. Redis 연결과 Consumer Group 생성을 확인하세요.</p>}<p className="muted small">Pending은 미확인 전달 수입니다. 실행 중인 논리 작업 수와 다를 수 있습니다.</p></section>
    <section className="panel padded"><p className="eyebrow">DEPENDENCIES</p><h2>서버 준비 상태</h2>{ready.data ? Object.entries(ready.data.checks).map(([name, ok]) => <div className="check-row" key={name}><span>{name.replaceAll('_', ' ')}</span><span className={ok ? 'good' : 'bad'}>{ok ? '준비됨' : '확인 필요'}</span></div>) : <p className="muted">의존성 준비 상태를 확인할 수 없습니다.</p>}<p className="muted small">API 준비 상태와 워커 생존 상태는 별도로 확인합니다.</p></section></div>
    <section className="panel"><div className="panel-heading"><h2>워커 heartbeat</h2><span className="muted small">최근 1시간 내 관측</span></div>{s.workers.length ? <div className="table-scroll"><table><thead><tr><th>워커</th><th>상태</th><th>마지막 heartbeat</th><th>작업</th></tr></thead><tbody>{s.workers.map(w => <tr key={w.worker_id}><td className="mono">{w.worker_id}</td><td><span className={w.online ? 'good' : 'muted'}>● {w.online ? 'ONLINE' : 'STALE'}</span></td><td>{time(w.seen_at)}</td><td>{w.current_job_id ? <Link className="mono" to={`/jobs/${w.current_job_id}`}>{w.current_job_id.slice(0, 8)} ↗</Link> : '—'}</td></tr>)}</tbody></table></div> : <div className="empty">관측된 워커가 없습니다. Worker 서비스를 시작하세요.</div>}</section>
    <section className="panel padded"><h2>최근 복구 및 실패 기록</h2>{s.recoveries.length ? s.recoveries.map((e, i) => <div className="recovery-row" key={`${e.job_id}-${i}`}><strong>{e.event_type}</strong><Link className="mono" to={`/jobs/${e.job_id}`}>{e.job_id.slice(0, 8)} ↗</Link><time className="muted">{time(e.created_at)}</time></div>) : <p className="muted">아직 기록된 복구 또는 실패 이벤트가 없습니다.</p>}</section></>}
  </>;
}
