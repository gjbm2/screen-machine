import { useState, useEffect, useCallback, useRef } from 'react';
import destinations from '../data/publish-destinations.json';

const API = '/api/admin-k9x7m';
const POLL_INTERVAL = 120_000; // 2 minutes

// Screens that have a /display/ page
const displayScreens = destinations.filter(
  d => d.has_bucket && !d.hidden && d.id !== '_recent'
);

interface ApiResult {
  success: boolean;
  output: string;
  error: string | null;
  timestamp: string;
}

interface LogEntry {
  action: string;
  result: ApiResult;
}

function parseStatus(output: string) {
  const get = (key: string) => {
    const m = output.match(new RegExp(`^${key}=(.*)$`, 'm'));
    return m ? m[1] : null;
  };
  const displays = [...output.matchAll(/^DISPLAY_LINE=(.*)$/gm)].map(m => m[1]);
  return {
    uptime: get('UPTIME') || 'unknown',
    kiosk: get('KIOSK') || 'unknown',
    northChrome: get('NORTH_CHROME') || 'unknown',
    southChrome: get('SOUTH_CHROME') || 'unknown',
    northUrl: get('NORTH_URL') || '',
    southUrl: get('SOUTH_URL') || '',
    mem: get('MEM_PERCENT'),
    disk: get('DISK_PERCENT'),
    load: get('LOAD'),
    displays,
  };
}

export default function AdminPanel() {
  const [status, setStatus] = useState<ReturnType<typeof parseStatus> | null>(null);
  const [statusOk, setStatusOk] = useState<boolean | null>(null);
  const [loading, setLoading] = useState('');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [lastPoll, setLastPoll] = useState<string>('');
  const [northUrl, setNorthUrl] = useState('');
  const [southUrl, setSouthUrl] = useState('');
  const [northCustomUrl, setNorthCustomUrl] = useState('');
  const [southCustomUrl, setSouthCustomUrl] = useState('');
  const [lightData, setLightData] = useState<any>(null);
  const [maskData, setMaskData] = useState<Record<string, any>>({});
  const [overrides, setOverrides] = useState<Record<string, { active: boolean; override: number | null }>>({});
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const addLog = useCallback((action: string, result: ApiResult) => {
    setLogs(prev => [{ action, result }, ...prev].slice(0, 50));
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/status`);
      const data: ApiResult = await res.json();
      if (data.success) {
        const parsed = parseStatus(data.output);
        setStatus(parsed);
        setStatusOk(parsed.kiosk === 'active');
        if (parsed.northUrl && parsed.northUrl !== 'unknown') setNorthUrl(parsed.northUrl);
        if (parsed.southUrl && parsed.southUrl !== 'unknown') setSouthUrl(parsed.southUrl);
      } else {
        setStatusOk(false);
        setStatus(null);
      }
      addLog('Status check', data);
      setLastPoll(new Date().toLocaleTimeString());
    } catch (e) {
      setStatusOk(false);
      addLog('Status check', {
        success: false, output: '', error: String(e), timestamp: new Date().toISOString()
      });
    }
  }, [addLog]);

  const fetchLight = useCallback(async () => {
    try {
      const [lightRes, northMask, southMask, northOv, southOv] = await Promise.all([
        fetch('/api/lightsensor/lightsense'),
        fetch('/api/north-screen/mask'),
        fetch('/api/south-screen/mask'),
        fetch('/api/north-screen/brightness-override'),
        fetch('/api/south-screen/brightness-override'),
      ]);
      setLightData(await lightRes.json());
      setMaskData({
        'north-screen': await northMask.json(),
        'south-screen': await southMask.json(),
      });
      const northOvData = await northOv.json();
      const southOvData = await southOv.json();
      setOverrides({
        'north-screen': northOvData,
        'south-screen': southOvData,
      });
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchLight();
    pollRef.current = setInterval(() => { fetchStatus(); fetchLight(); }, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [fetchStatus, fetchLight]);

  const doAction = async (endpoint: string, label: string, body?: object) => {
    setLoading(label);
    try {
      const res = await fetch(`${API}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data: ApiResult = await res.json();
      addLog(label, data);
    } catch (e) {
      addLog(label, {
        success: false, output: '', error: String(e), timestamp: new Date().toISOString()
      });
    }
    setLoading('');
    // Refresh status after action
    setTimeout(fetchStatus, 3000);
  };

  const Dot = ({ ok }: { ok: boolean | null }) => (
    <span style={{
      display: 'inline-block', width: 12, height: 12, borderRadius: '50%',
      background: ok === null ? '#666' : ok ? '#22c55e' : '#ef4444',
      marginRight: 8,
    }} />
  );

  const btnBase: React.CSSProperties = {
    width: '100%', padding: '14px 0', borderRadius: 8, border: 'none',
    fontSize: 16, fontWeight: 600, cursor: 'pointer', marginBottom: 10,
    color: '#fff', opacity: loading ? 0.6 : 1,
  };

  return (
    <div style={{
      minHeight: '100vh', background: '#111', color: '#e5e5e5',
      fontFamily: 'system-ui, sans-serif', padding: 16, maxWidth: 480, margin: '0 auto',
    }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>Media Server Admin</h1>
      <p style={{ fontSize: 12, color: '#888', marginBottom: 16 }}>
        Auto-polls every 2 min · Last: {lastPoll || 'loading...'}
      </p>

      {/* Status Card */}
      <div style={{
        background: '#1a1a1a', borderRadius: 10, padding: 16, marginBottom: 20,
        border: `1px solid ${statusOk === null ? '#333' : statusOk ? '#22c55e44' : '#ef444444'}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
          <Dot ok={statusOk} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>
            {statusOk === null ? 'Checking...' : statusOk ? 'Healthy' : 'Unhealthy'}
          </span>
        </div>
        {status ? (
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <div><b>Kiosk:</b> {status.kiosk}</div>
            <div><b>Uptime:</b> {status.uptime}</div>
            <div><b>North Screen:</b> {status.northChrome}</div>
            <div style={{ fontSize: 11, color: '#888', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{status.northUrl}</div>
            <div><b>South Screen:</b> {status.southChrome}</div>
            <div style={{ fontSize: 11, color: '#888', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{status.southUrl}</div>
            <div><b>Memory:</b> {status.mem}% · <b>Disk:</b> {status.disk}% · <b>Load:</b> {status.load}</div>
            <div><b>Displays:</b> {status.displays.length > 0 ? status.displays.join(', ') : 'none detected'}</div>
          </div>
        ) : (
          <div style={{ color: '#888' }}>No data yet</div>
        )}
        <button
          onClick={() => fetchStatus()}
          style={{ ...btnBase, background: '#333', marginTop: 10, marginBottom: 0 }}
        >
          Refresh Now
        </button>
      </div>

      {/* Sensor readout */}
      {lightData?.sensors && Object.keys(lightData.sensors).length > 0 && (
        <div style={{
          background: '#1a1a1a', borderRadius: 10, padding: 12, marginBottom: 12,
          border: '1px solid #333', fontSize: 13,
        }}>
          {Object.entries(lightData.sensors).map(([name, sensor]: [string, any]) => (
            <div key={name}>
              <b>Sensor:</b> {sensor.current?.lux != null ? `${Math.round(sensor.current.lux)} lux` : 'no data'}
              {sensor.current?.target_intensity != null && (
                <span style={{ color: '#888' }}> → {Math.round(sensor.current.target_intensity * 100)}%</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Per-screen controls: URL + Brightness */}
      {(['north', 'south'] as const).map(screen => {
        const screenId = `${screen}-screen`;
        const url = screen === 'north' ? northUrl : southUrl;
        const setUrl = screen === 'north' ? setNorthUrl : setSouthUrl;
        const customUrl = screen === 'north' ? northCustomUrl : southCustomUrl;
        const setCustomUrl = screen === 'north' ? setNorthCustomUrl : setSouthCustomUrl;
        const currentUrl = status ? (screen === 'north' ? status.northUrl : status.southUrl) : '';
        const defaultUrl = `${window.location.origin}/display/${screen}-screen`;
        const changed = url !== currentUrl && url !== '';
        const urlPath = url.replace(/^https?:\/\/[^/]+/, '');
        const matchedScreen = displayScreens.find(d => urlPath === `/display/${d.id}`);
        const selectValue = matchedScreen ? matchedScreen.id : '__custom__';

        const mask = maskData[screenId];
        const ov = overrides[screenId];
        const isOverridden = ov?.active;
        const currentBrightness = mask?.brightness ?? 1;

        return (
          <div key={screen} style={{
            background: '#1a1a1a', borderRadius: 10, padding: 14, marginBottom: 12,
            border: '1px solid #333',
          }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, textTransform: 'capitalize' }}>
              {screen} Screen
            </div>

            {/* URL control */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <select
                value={selectValue}
                onChange={e => {
                  const val = e.target.value;
                  if (val === '__custom__') {
                    setUrl(customUrl || '');
                  } else {
                    setUrl(`${window.location.origin}/display/${val}`);
                  }
                }}
                style={{
                  flex: 1, padding: '10px 8px', borderRadius: 6,
                  border: '1px solid #444', background: '#222', color: '#e5e5e5',
                  fontSize: 14, boxSizing: 'border-box', appearance: 'auto',
                }}
              >
                {displayScreens.map(d => (
                  <option key={d.id} value={d.id}>
                    {d.name}{d.id === screenId ? ' (default)' : ''}
                  </option>
                ))}
                <option value="__custom__">Custom URL</option>
              </select>
              <button
                onClick={() => doAction('set-url', `Set ${screen} URL`, { screen, url })}
                disabled={!!loading || !changed || !url}
                style={{
                  ...btnBase, flex: 0, whiteSpace: 'nowrap', padding: '10px 16px',
                  background: (changed && url) ? '#7c3aed' : '#333',
                  marginBottom: 0, opacity: (!changed || !url || loading) ? 0.5 : 1,
                }}
              >
                {loading === `Set ${screen} URL` ? '...' : 'Set'}
              </button>
              {selectValue !== screenId && (
                <button
                  onClick={() => {
                    setUrl(defaultUrl);
                    doAction('set-url', `Reset ${screen} URL`, { screen, url: defaultUrl });
                  }}
                  disabled={!!loading}
                  style={{ ...btnBase, flex: 0, whiteSpace: 'nowrap', padding: '10px 12px', background: '#555', marginBottom: 0 }}
                >
                  Reset
                </button>
              )}
            </div>
            {selectValue === '__custom__' && (
              <input
                type="url"
                value={url}
                onChange={e => { setUrl(e.target.value); setCustomUrl(e.target.value); }}
                placeholder="https://..."
                style={{
                  width: '100%', padding: '10px 8px', borderRadius: 6,
                  border: '1px solid #444', background: '#222', color: '#e5e5e5',
                  fontSize: 13, marginBottom: 8, boxSizing: 'border-box',
                }}
              />
            )}

            {/* Brightness control */}
            <div style={{ borderTop: '1px solid #333', paddingTop: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, flex: 1 }}>
                  <b>Brightness</b>{' '}
                  <span style={{ color: '#888', fontSize: 11 }}>
                    {isOverridden ? 'manual' : mask?.source === 'sensor' ? 'sensor' : 'auto'}
                  </span>
                </span>
                <span style={{ fontSize: 13, fontFamily: 'monospace' }}>{Math.round(currentBrightness * 100)}%</span>
                {isOverridden && (
                  <button
                    onClick={async () => {
                      await fetch(`/api/${screenId}/brightness-override`, { method: 'DELETE' });
                      fetchLight();
                    }}
                    style={{
                      background: '#555', border: 'none', color: '#ccc', borderRadius: 4,
                      padding: '2px 8px', fontSize: 11, cursor: 'pointer',
                    }}
                  >
                    auto
                  </button>
                )}
              </div>
              <input
                type="range"
                min={0} max={100} step={1}
                value={Math.round(currentBrightness * 100)}
                onChange={async e => {
                  const val = parseInt(e.target.value) / 100;
                  setMaskData(prev => ({ ...prev, [screenId]: { ...prev[screenId], brightness: val } }));
                  setOverrides(prev => ({ ...prev, [screenId]: { active: true, override: val } }));
                  await fetch(`/api/${screenId}/brightness-override`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ brightness: val }),
                  });
                }}
                style={{ width: '100%' }}
              />
            </div>
          </div>
        );
      })}

      {/* Action Buttons */}
      <div style={{ marginBottom: 20 }}>
        <button
          onClick={() => doAction('restart-kiosk', 'Restart Kiosk (All)')}
          disabled={!!loading}
          style={{ ...btnBase, background: '#d97706' }}
        >
          {loading === 'Restart Kiosk (All)' ? 'Restarting...' : 'Restart Kiosk (All Screens)'}
        </button>
        <button
          onClick={() => doAction('reboot', 'Reboot Server')}
          disabled={!!loading}
          style={{ ...btnBase, background: '#dc2626' }}
        >
          {loading === 'Reboot Server' ? 'Rebooting...' : 'Reboot Server'}
        </button>
      </div>

      {/* Output Log */}
      <div>
        <h2 style={{ fontSize: 14, marginBottom: 8 }}>Command Log</h2>
        <div style={{
          background: '#0a0a0a', borderRadius: 8, padding: 12, maxHeight: 400,
          overflow: 'auto', fontSize: 12, fontFamily: 'monospace',
        }}>
          {logs.length === 0 && <div style={{ color: '#666' }}>No actions yet</div>}
          {logs.map((l, i) => (
            <div key={i} style={{ marginBottom: 12, borderBottom: '1px solid #222', paddingBottom: 8 }}>
              <div style={{ color: l.result.success ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                [{new Date(l.result.timestamp).toLocaleTimeString()}] {l.action}: {l.result.success ? 'OK' : 'FAILED'}
              </div>
              {l.result.output && (
                <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', color: '#aaa' }}>
                  {l.result.output.trim()}
                </pre>
              )}
              {l.result.error && (
                <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', color: '#f87171' }}>
                  {l.result.error}
                </pre>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
