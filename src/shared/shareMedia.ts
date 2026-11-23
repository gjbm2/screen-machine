// Share a picture or clip as the file itself, through the device's own share sheet (WhatsApp, Messages, AirDrop, Mail...),
// owner 23 Nov. Sending the file, not its link: a link to the house's server means nothing to the person it is sent to.
//
// The share sheet needs a fresh tap, and fetching a big clip can take longer than the browser counts a tap as fresh
// (iOS especially). So the fetched file is kept, and 'needs-tap' tells the caller to offer a second tap that shares it
// at once. Where the browser can't share files (desktop Firefox, plain http on the LAN) the file is downloaded instead.

export type ShareOutcome = 'shared' | 'cancelled' | 'needs-tap' | 'downloaded';

const MIME: Record<string, string> = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif',
  mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime',
};

/** The file's name from its URL: the last path segment, without a query or a '#dup' tag. */
export function fileNameOf(url: string): string {
  const path = url.split(/[?#]/)[0];
  const last = path.slice(path.lastIndexOf('/') + 1);
  try { return decodeURIComponent(last) || 'picture'; } catch { return last || 'picture'; }
}

// the last file fetched (one at a time: a clip can be tens of MB)
let kept: { url: string; file: File } | null = null;

async function fileFor(url: string): Promise<File> {
  if (kept?.url === url) return kept.file;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Couldn't fetch it (${res.status})`);
  const blob = await res.blob();
  const name = fileNameOf(url);
  const ext = name.split('.').pop()?.toLowerCase() || '';
  // the share targets go by the file's type; a server's octet-stream would make WhatsApp send it as a document
  const type = blob.type && blob.type !== 'application/octet-stream' ? blob.type : (MIME[ext] || blob.type);
  const file = new File([blob], name, { type });
  kept = { url, file };
  return file;
}

function download(file: File) {
  const href = URL.createObjectURL(file);
  const a = document.createElement('a');
  a.href = href;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

/** Can this browser offer the share sheet at all? (Secure pages on phones and most desktops.) */
export const canShareSheet = (): boolean => typeof navigator !== 'undefined' && typeof navigator.share === 'function';

export async function shareMedia(url: string): Promise<ShareOutcome> {
  const file = await fileFor(url);
  const data: ShareData = { files: [file] };
  if (canShareSheet() && navigator.canShare?.(data)) {
    try {
      // the file alone: a title or text alongside it arrives as a separate message in WhatsApp
      await navigator.share(data);
      return 'shared';
    } catch (err) {
      const name = (err as DOMException)?.name;
      if (name === 'AbortError') return 'cancelled';
      if (name === 'NotAllowedError') return 'needs-tap';
      throw err;
    }
  }
  download(file);
  return 'downloaded';
}
