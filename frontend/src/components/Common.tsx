import { Link } from 'react-router-dom';
import type { Job, Status } from '../types';
import { bytes, duration, time } from '../lib/api';
import { statusLabels } from '../lib/status';

export function Badge({ status }: { status: Status }) { return <span className={`badge ${status.toLowerCase()}`}><i />{statusLabels[status]}</span>; }
export function PageHeading({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1>{description && <p className="muted">{description}</p>}</div>{action}</header>;
}
export function ErrorState({ error, retry }: { error: Error | null; retry?: () => void }) {
  return error ? <div role="alert" className="error-box"><strong>요청 실패</strong><p>{error.message === 'Failed to fetch' ? '서버에 연결할 수 없습니다.' : error.message}</p>{retry && <button className="button secondary" onClick={retry}>다시 시도</button>}</div> : null;
}
export function Loading() { return <div className="empty" role="status">불러오는 중…</div>; }
export function JobTable({ jobs }: { jobs: Job[] }) {
  return !jobs.length ? <div className="empty"><h3>등록된 작업이 없습니다</h3><Link to="/new" className="button">CSV 업로드</Link></div> : <div className="table-scroll"><table><thead><tr><th>파일 / 작업 ID</th><th>상태</th><th>처리한 행</th><th>처리 시간</th><th>등록일</th><th /></tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td><Link className="file-link" to={`/jobs/${job.job_id}`}>{job.original_filename}</Link><small className="mono">{job.job_id.slice(0, 8)} · {bytes(job.file_size)}</small></td><td><Badge status={job.status} /></td><td className="mono">{job.processed_rows.toLocaleString()}{job.total_rows !== null && ` / ${job.total_rows.toLocaleString()}`}</td><td className="mono">{duration(job.duration_ms)}</td><td className="muted nowrap">{time(job.created_at)}</td><td><Link aria-label={`${job.original_filename} 상세`} to={`/jobs/${job.job_id}`}>↗</Link></td></tr>)}</tbody></table></div>;
}
