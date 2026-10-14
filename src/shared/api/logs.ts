import { send } from '@/shared/http/client';

// Never throws: non-2xx, network and JSON-parse errors all return { success: false, logs: [] }.
export async function getLogs(limit: number = 100) {
  try {
    const response = await send(`/logs?limit=${limit}`, { method: 'GET' });

    if (!response.ok) {
      console.error('Failed to get logs');
      return { success: false, logs: [] };
    }

    return await response.json();
  } catch (error) {
    console.error('Error getting logs:', error);
    return { success: false, logs: [] };
  }
}
