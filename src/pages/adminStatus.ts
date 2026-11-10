// The admin page's kiosk status, parsed (pure, so plain node can test it: adminStatus.test.ts).

// The kiosk status script (routes/admin_api.py status_script) writes one block per kiosk screen, its variables named
// by the screen's short name upper-cased (NORTH_CHROME, NORTH_URL for "north";
// NORTH_TV_CHROME for "north_tv": a name may hold "_"): read by name, never a list here.
export function parseStatus(output: string) {
  const get = (key: string) => {
    const m = output.match(new RegExp(`^${key}=(.*)$`, 'm'));
    return m ? m[1] : null;
  };
  const displays = [...output.matchAll(/^DISPLAY_LINE=(.*)$/gm)].map(m => m[1]);
  const chrome: Record<string, string> = {};
  const urls: Record<string, string> = {};
  for (const m of output.matchAll(/^([A-Z][A-Z0-9_]*)_(CHROME|URL)=(.*)$/gm)) {
    if (m[2] === 'CHROME') chrome[m[1].toLowerCase()] = m[3] || 'unknown';
    else urls[m[1].toLowerCase()] = m[3] || '';
  }
  return {
    uptime: get('UPTIME') || 'unknown',
    kiosk: get('KIOSK') || 'unknown',
    chrome,
    urls,
    mem: get('MEM_PERCENT'),
    disk: get('DISK_PERCENT'),
    load: get('LOAD'),
    displays,
  };
}
