import { useId } from 'react';
import type { CSSProperties } from 'react';
import { duration } from '../lib/api';
import type { Job, Stats } from '../types';

export default function ProcessingCharts({ stats, jobs }: { stats: Stats; jobs: Job[] | undefined }) {
  const chartId = useId();
  const groups = [
    { label: '완료', count: stats.counts.SUCCEEDED, color: '#bd81ff', className: 'success' },
    { label: '진행·대기', count: stats.counts.RUNNING + stats.counts.QUEUED + stats.counts.RETRY_WAIT, color: '#43d9ed', className: 'active' },
    { label: '실패', count: stats.counts.FAILED, color: '#ff82a8', className: 'failed' },
    { label: '취소', count: stats.counts.CANCELLED, color: '#f6bc73', className: 'cancelled' },
  ];
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  let angle = 0;
  const segments = groups.map(group => {
    const start = angle;
    angle += total ? group.count / total * 360 : 0;
    return `${group.color} ${start}deg ${angle}deg`;
  });
  const completed = (jobs ?? []).filter(job => job.duration_ms !== null).reverse();
  const maximumDuration = Math.max(1, ...completed.map(job => job.duration_ms ?? 0));

  return <div className="charts-grid">
    <section className="panel chart-panel" aria-labelledby={`${chartId}-duration`}>
      <div className="panel-heading">
        <div><h2 id={`${chartId}-duration`}>처리 시간</h2><p className="muted small">최근 6개 작업 기준</p></div>
        <span className="chart-unit">초</span>
      </div>
      {completed.length ? <div className="bar-chart">
        <div className="chart-scale" aria-hidden="true"><span>{(maximumDuration / 1000).toFixed(1)}</span><span>{(maximumDuration / 2000).toFixed(1)}</span><span>0</span></div>
        <ul className="chart-bars" aria-label="작업별 처리 시간">
          {completed.map(job => <li className="chart-column" key={job.job_id}>
            <div className="bar-track"><div className="chart-bar" style={{ '--bar-height': `${(job.duration_ms ?? 0) / maximumDuration * 100}%` } as CSSProperties}>
              <span className="bar-value">{duration(job.duration_ms)}</span>
            </div></div>
            <span className="bar-label" title={job.original_filename}>{job.original_filename}</span>
          </li>)}
        </ul>
      </div> : <div className="empty chart-empty">{jobs ? '처리 시간 기록이 없습니다.' : '불러오는 중…'}</div>}
    </section>
    <section className="panel chart-panel" aria-labelledby={`${chartId}-status`}>
      <div className="panel-heading"><h2 id={`${chartId}-status`}>작업 상태</h2></div>
      <div className="distribution-chart">
        <div className={`donut-chart${total ? '' : ' no-data'}`} style={{ '--donut-fill': `conic-gradient(from -90deg, ${segments.join(', ')})` } as CSSProperties} role="img" aria-label={total ? `전체 ${total}개 중 ${stats.counts.SUCCEEDED}개 완료, ${Math.round(stats.counts.SUCCEEDED / total * 100)}%` : '등록된 작업이 없습니다'}>
          <div className="donut-center"><strong>{total ? `${Math.round(stats.counts.SUCCEEDED / total * 100)}%` : '—'}</strong><span>완료 비율</span></div>
        </div>
        <ul className="chart-legend" aria-label="작업 상태별 개수">
          {groups.map(group => <li key={group.label}><span className={`legend-dot ${group.className}`} /><span>{group.label}</span><strong>{group.count.toLocaleString()}</strong></li>)}
        </ul>
      </div>
    </section>
  </div>;
}
