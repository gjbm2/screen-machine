// What this browser can decode through MSE, reported in `hello.caps`.
import type { Caps } from "./protocol";

// Representative codec strings: the stream_service profiles (4K60 HEVC Main, 720p H.264, AV1 Main 8-bit).
const PROBES: Record<keyof Caps, string[]> = {
  mse_hevc: ['video/mp4; codecs="hvc1.1.6.L153.B0"', 'video/mp4; codecs="hev1.1.6.L153.B0"'],
  mse_h264: ['video/mp4; codecs="avc1.640033"', 'video/mp4; codecs="avc1.64001f"', 'video/mp4; codecs="avc1.42E01E"'],
  mse_av1: ['video/mp4; codecs="av01.0.12M.08"', 'video/mp4; codecs="av01.0.05M.08"'],
};

export function mseSupported(mime: string): boolean {
  try {
    return typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(mime);
  } catch {
    return false;
  }
}

export function probeCaps(): Caps {
  const caps = { mse_hevc: false, mse_h264: false, mse_av1: false };
  for (const key of Object.keys(PROBES) as (keyof Caps)[]) {
    caps[key] = PROBES[key].some(mseSupported);
  }
  return caps;
}

/** MSE MIME type for a stream: `codec_string` from the media socket's config, else a family default. */
export function mimeFor(codec: string, codecString?: string): string {
  if (codecString) return `video/mp4; codecs="${codecString}"`;
  const fallback: Record<string, string> = { hevc: "hvc1.1.6.L153.B0", h264: "avc1.640033", av1: "av01.0.12M.08" };
  return `video/mp4; codecs="${fallback[codec] ?? codec}"`;
}
