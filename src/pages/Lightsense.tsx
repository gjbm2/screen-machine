import React, { useEffect, useState } from 'react';
import apiService from '@/utils/api';

interface SensorData {
  current: {
    lux: number | null;
    target_intensity: number | null;
    target_group: string | null;
  };
  history: Array<{
    timestamp: number;
    lux: number;
    target_intensity: number | null;
  }>;
}

interface SensorsData {
  sensors: {
    [key: string]: SensorData;
  };
}

const Lightsense: React.FC = () => {
  const [sensorsData, setSensorsData] = useState<SensorsData | null>(null);

  useEffect(() => {
    let isMounted = true;
    const fetchData = async () => {
      try {
        const data = await apiService.getLightsense();
        if (isMounted) {
          setSensorsData(data);
        }
      } catch (err) {
        console.error('Error fetching sensor data:', err);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 1000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  if (!sensorsData) {
    return <div style={{ padding: 32 }}>Loading...</div>;
  }

  return (
    <div style={{ padding: 32 }}>
      <h1>Light Sensors</h1>
      {Object.entries(sensorsData.sensors).map(([sensorName, data]) => (
        <div key={sensorName} style={{ marginBottom: 32 }}>
          <h2>{sensorName}</h2>
          <div style={{ fontSize: 24, margin: '16px 0' }}>
            <div>Current Lux: {data.current.lux?.toFixed(2) ?? 'N/A'}</div>
            <div>Target Intensity: {data.current.target_intensity?.toFixed(2) ?? 'N/A'}</div>
            {data.current.target_group && (
              <div>Target Group: {data.current.target_group}</div>
            )}
          </div>
          <h3>History (last {data.history.length} entries)</h3>
          <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid #ccc', padding: 8 }}>
            <ul style={{ fontFamily: 'monospace', fontSize: 14 }}>
              {data.history.slice(-30).reverse().map((entry, idx) => (
                <li key={idx}>
                  {new Date(entry.timestamp * 1000).toLocaleTimeString()} — 
                  Lux: {entry.lux.toFixed(2)} — 
                  Intensity: {entry.target_intensity?.toFixed(2) ?? 'N/A'}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ))}
    </div>
  );
};

export default Lightsense; 