import { requestJson } from '@/shared/http/client';
import type { TranscriptionStatus } from './types';

export async function getTranscriptionStatus(): Promise<TranscriptionStatus> {
  return requestJson('/transcription-status', undefined, 'Failed to get transcription status');
}
