// File operations for scheduler scripts and config files (Flask file_bp at /api/files).

import { jsonInit, requestJson } from '@/shared/http/client';

export async function listFiles(directory: string): Promise<string[]> {
  const data = await requestJson(
    `/files?directory=${encodeURIComponent(directory)}`,
    undefined,
    (response) => `Failed to list files: ${response.status}`,
  );
  return data.files || [];
}

// The whole path is encoded ('/' becomes %2F); the Flask <path:file_path> route still matches.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function readFile(filePath: string): Promise<any> {
  return requestJson(
    `/files/${encodeURIComponent(filePath)}`,
    undefined,
    (response) => `Failed to read file: ${response.status}`,
  );
}

export async function writeFile(path: string, content: string): Promise<{ success: boolean; path: string }> {
  return requestJson(
    '/files',
    jsonInit('POST', { path, content }),
    (response) => `Failed to write file: ${response.status}`,
  );
}
