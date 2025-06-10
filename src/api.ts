export interface TranscriptionStatus {
  is_active: boolean;
  client_ids: string[];
  target: string;
  current_transcription: string;
  last_event_time: string | null;
  last_event_word_count: number;
}

export const getTranscriptionStatus = async (): Promise<TranscriptionStatus> => {
  const response = await fetch(`${import.meta.env.VITE_API_URL}/transcription-status`);
  if (!response.ok) {
    throw new Error('Failed to get transcription status');
  }
  return response.json();
}; 