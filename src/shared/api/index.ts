// The Flask API, one module per domain: `import { bucketsApi } from '@/shared/api'`.
export * as adminApi from './admin';
export * as audioApi from './audio';
export * as bucketsApi from './buckets';
export * as eventsApi from './events';
export * as filesApi from './files';
export * as generationApi from './generation';
export * as lightApi from './light';
export * as logsApi from './logs';
export * as publishApi from './publish';
export * as schedulerApi from './scheduler';
export * as varsApi from './vars';
export type * from './types';
export { API_URL, apiUrl } from '@/shared/http/client';
