// Error toasts some API calls have always raised themselves. Internal: not exported from the barrel.
// They fire wherever the call runs, the display page included; moving them into callers is REFACTOR_PLAN T7.

import { toast } from 'sonner';
import { requestWithErrorBody } from '@/shared/http/client';

/** sonner toast on any failure (non-2xx, network or parse error), then rethrow. */
export async function toastOnFailure<T>(message: string, run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    toast.error(message);
    throw error;
  }
}

/** requestWithErrorBody + JSON body, toasting `message` on failure; `message` is also the error fallback. */
export async function toastedJson(path: string, init: RequestInit | undefined, message: string) {
  return toastOnFailure(message, async () => (await requestWithErrorBody(path, init, message)).json());
}
