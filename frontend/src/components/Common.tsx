import { Link } from 'react-router-dom';
import type { Job, Status } from '../types';
import { bytes, duration, time } from '../lib/api';

export function Badge({ status }: { status: Status }) { return <span className={`badge ${status.toLowerCase()}`}><i />{status}</span>; }
export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <header className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="muted">{description}</p></div>{action}</header>;
}
export function ErrorState({ error, retry }: { error: Error | null; retry?: () => void }) {
  return error ? <div role="alert" className="error-box"><strong>데이터를 불러오지 못했습니다.</strong><p>{error.message}</p>{retry && <button className="button secondary" onClick={retry}>다시 연결</button>}</div> : null;
}
export function Loading() { return <div className="empty" role="status">데이터를 확인하고 있습니다…</div>; }
export function JobTable({ jobs }: { jobs: Job[] }) {
  return !jobs.length ? <div className="empty"><span className="empty-mark">↗</span><h3>아직 등록된 작업이 없습니다</h3><p>CSV를 업로드하면 이곳에서 처리 상태를 확인할 수 있습니다.</p><Link to="/new" className="button">첫 작업 등록</Link></div> : <div className="table-scroll"><table><thead><tr><th>파일 / 작업 ID</th><th>상태</th><th>처리 행</th><th>처리 시간</th><th>등록 시각</th><th /></tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td><Link className="file-link" to={`/jobs/${job.job_id}`}>{job.original_filename}</Link><small className="mono">{job.job_id.slice(0, 8)} · {bytes(job.file_size)}</small></td><td><Badge status={job.status} /></td><td className="mono">{job.processed_rows.toLocaleString()}{job.total_rows !== null && ` / ${job.total_rows.toLocaleString()}`}</td><td className="mono">{duration(job.duration_ms)}</td><td className="muted nowrap">{time(job.created_at)}</td><td><Link aria-label={`${job.original_filename} 상세`} to={`/jobs/${job.job_id}`}>↗</Link></td></tr>)}</tbody></table></div>;
}
