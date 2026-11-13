import type { Status } from '../types';

export const statusLabels: Record<Status, string> = {
  QUEUED: '대기',
  RUNNING: '처리 중',
  RETRY_WAIT: '재시도 대기',
  SUCCEEDED: '완료',
  FAILED: '실패',
  CANCELLED: '취소',
};

export const eventLabels: Record<string, string> = {
  JOB_CREATED: '작업 등록',
  WORKER_STARTED: '처리 시작',
  JOB_COMPLETED: '처리 완료',
  JOB_FAILED: '처리 실패',
  JOB_CANCELLED: '작업 취소',
  CANCELLATION_REQUESTED: '취소 요청',
  RETRY_SCHEDULED: '재시도 예약',
  RETRY_READY: '재시도 대기 종료',
  WORKER_LEASE_EXPIRED: '워커 응답 시간 초과',
  JOB_RECLAIMED: '워커 재할당',
  MESSAGE_PUBLISHED: '큐 메시지 발행',
  DELIVERY_RECONCILED: '메시지 재전달',
  FILES_EXPIRED: '보관 기간 만료',
};

export const terminal = (status?: Status) => !!status && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status);
