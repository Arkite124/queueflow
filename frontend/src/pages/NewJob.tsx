import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ErrorState, PageHeading } from '../components/Common';
import { api, bytes } from '../lib/api';
import type { Job } from '../types';

const defaults = { required_columns: ['id', 'email', 'created_at'], required_values: ['id', 'email'], trim_whitespace: true, remove_empty_rows: true, normalize_columns: true, duplicate_columns: ['id'], remove_duplicates: true, invalid_rows: 'exclude', safe_export: true, column_types: { id: 'integer', email: 'email', created_at: 'date' } };

export default function NewJob() {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const [rules, setRules] = useState(JSON.stringify(defaults, null, 2));
  const [localError, setLocalError] = useState<Error | null>(null);
  const key = useRef(crypto.randomUUID());
  const navigate = useNavigate();
  const client = useQueryClient();
  const upload = useMutation({ mutationFn: async () => {
    if (!file) throw new Error('CSV 파일을 선택해주세요.');
    if (file.size > 20 * 1024 * 1024) throw new Error('최대 파일 크기는 20 MiB입니다.');
    JSON.parse(rules);
    const form = new FormData(); form.append('file', file); form.append('options', rules);
    return api<Job>('/api/v1/jobs', { method: 'POST', body: form, headers: { 'Idempotency-Key': key.current } });
  }, onSuccess: (job) => { void client.invalidateQueries({ queryKey: ['jobs'] }); void client.invalidateQueries({ queryKey: ['stats'] }); navigate(`/jobs/${job.job_id}`); } });
  function selectFiles(files: File[]) {
    if (upload.isPending) return;
    const next = files[0] ?? null;
    const error = files.length !== 1 ? 'CSV 파일 한 개를 선택해주세요.'
      : next && !next.name.toLowerCase().endsWith('.csv') ? 'CSV 확장자 파일을 선택해주세요.'
      : next && next.size === 0 ? '빈 파일은 업로드할 수 없습니다.'
      : next && next.size > 20 * 1024 ** 2 ? '파일 크기가 20 MiB를 초과했습니다.' : null;
    setFile(error ? null : next);
    key.current = crypto.randomUUID();
    setLocalError(error ? new Error(error) : null);
    upload.reset();
  }
  function sample() {
    const csv = 'id,email,created_at\n1, demo1@example.test ,2026-01-01\n2,invalid-email,2026-01-02\n1,duplicate@example.test,2026-01-03\n3,demo3@example.test,2026-01-04\n';
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = 'queueflow-sample.csv'; a.click(); URL.revokeObjectURL(url);
  }
  return <><PageHeading title="새 작업" description="CSV 파일을 선택하고 처리 규칙을 확인하세요." />
    <form onSubmit={e => { e.preventDefault(); setLocalError(null); upload.mutate(); }} className="upload-layout"><div className="stack"><section className="panel padded"><div className="step-title"><h2>CSV 파일 선택</h2></div><label className={`upload-zone${dragging ? ' dragging' : ''}`} onDragEnter={e => { e.preventDefault(); if (!upload.isPending && e.dataTransfer.types.includes('Files')) { dragDepth.current += 1; setDragging(true); } }} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = upload.isPending ? 'none' : 'copy'; }} onDragLeave={e => { e.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }} onDrop={e => { e.preventDefault(); dragDepth.current = 0; setDragging(false); if (!upload.isPending) selectFiles(Array.from(e.dataTransfer.files)); }}><span className="upload-icon">↥</span><strong>{file ? file.name : 'CSV를 끌어놓거나 파일을 선택하세요'}</strong><span className="muted">{file ? bytes(file.size) : 'UTF-8 / UTF-8 BOM · 최대 20 MiB · 100,000행'}</span><input type="file" accept=".csv,text/csv" aria-label="CSV 파일" disabled={upload.isPending} onChange={e => { selectFiles(Array.from(e.target.files ?? [])); e.target.value = ''; }} /></label><button type="button" className="text-link" onClick={sample}>예제 CSV 다운로드 ↓</button></section>
    <section className="panel padded"><div className="step-title"><h2>처리 규칙</h2></div><label>처리 규칙 (JSON)<textarea className="rules mono" spellCheck={false} value={rules} disabled={upload.isPending} onChange={e => { setRules(e.target.value); key.current = crypto.randomUUID(); }} /></label><p className="muted small">컬럼명은 정규화 후 이름을 사용합니다. 날짜 형식은 YYYY-MM-DD입니다.</p></section>
    <ErrorState error={localError || upload.error} /><button className="button submit" type="submit" disabled={!file || !!localError || upload.isPending}>{upload.isPending ? '업로드 중…' : '처리 시작'}</button></div>
    <aside className="panel padded help-panel">
      <h2>다운로드 파일</h2>
      <p className="muted small">작업이 끝나면 개별 파일이나 ZIP으로 받을 수 있습니다.</p>
      <dl className="result-guide">
        <div><dt>cleaned.csv</dt><dd>처리 규칙을 적용한 데이터</dd></div>
        <div><dt>errors.csv</dt><dd>행별 오류 내역</dd></div>
        <div><dt>summary.json</dt><dd>처리 결과와 적용한 규칙</dd></div>
        <div><dt>report.xlsx</dt><dd>처리 건수와 오류를 정리한 Excel 파일</dd></div>
      </dl>
      <div className="notice"><strong>수식 셀 보호</strong><p>수식으로 읽힐 수 있는 값 앞에 작은따옴표를 붙입니다. 변경된 셀 수는 요약에 표시됩니다.</p></div>
      <p className="muted small">데모에는 샘플 데이터를 사용하세요.</p>
    </aside></form></>;
}
