import type { Status } from '../types';

export const terminal = (status?: Status) => !!status && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status);
