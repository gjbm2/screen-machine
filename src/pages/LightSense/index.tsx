import { useEffect, useState, useCallback } from "react";
import { Api } from "@/utils/api";
import { Line } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import dragDataPlugin from 'chartjs-plugin-dragdata';

// Register ChartJS components
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  dragDataPlugin
);

interface Point {
  lux: number;
  intensity: number;
}

interface SensorMapping {
  lux_to_intensity: Point[];
  target_group: string;
}

interface IntensitySettings {
  sensor_mappings: {
    [key: string]: SensorMapping;
  };
}

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

interface LightsenseData {
  sensors: {
    [key: string]: SensorData;
  };
}

export default function LightSense() {
  const [settings, setSettings] = useState<IntensitySettings | null>(null);
  const [sensorData, setSensorData] = useState<LightsenseData | null>(null);
  const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [targetGroup, setTargetGroup] = useState<string>("");
  const [isEditing, setIsEditing] = useState(false);
  const { toast } = useToast();
  const api = new Api();

  // Load settings and sensor data on mount
  useEffect(() => {
    loadSettings();
    loadSensorData();
    // Set up polling for sensor data
    const interval = setInterval(loadSensorData, 10000); // Poll every 10 seconds
    return () => clearInterval(interval);
  }, []);

  // Update points when selected sensor changes
  useEffect(() => {
    if (selectedSensor && settings?.sensor_mappings[selectedSensor]) {
      const mapping = settings.sensor_mappings[selectedSensor];
      setPoints([...mapping.lux_to_intensity]);
      setTargetGroup(mapping.target_group);
    }
  }, [selectedSensor, settings]);

  const loadSettings = async () => {
    try {
      const response = await api.getIntensitySettings();
      setSettings(response);
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to load intensity settings",
        variant: "destructive",
      });
    }
  };

  const loadSensorData = async () => {
    try {
      const response = await api.getLightsense();
      setSensorData(response);
    } catch (error) {
      console.error('Error loading sensor data:', error);
    }
  };

  const handleSave = async () => {
    if (!selectedSensor) return;

    try {
      await api.updateIntensitySettings(selectedSensor, {
        points,
        target_group: targetGroup,
      });

      // Reload settings to get updated data
      await loadSettings();
      setIsEditing(false);
      toast({
        title: "Success",
        description: "Intensity settings updated",
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to update intensity settings",
        variant: "destructive",
      });
    }
  };

  const handleAddPoint = () => {
    setPoints([...points, { lux: 0, intensity: 0 }]);
  };

  const handleDeletePoint = (index: number) => {
    setPoints(points.filter((_, i) => i !== index));
  };

  const handlePointChange = (index: number, field: keyof Point, value: number) => {
    const newPoints = [...points];
    newPoints[index] = { ...newPoints[index], [field]: value };
    setPoints(newPoints);
  };

  // Click handler: click on a point removes it, click on empty area adds a point
  const handleChartClick = (event: any, elements: any, chart: any) => {
    if (!chart) return;
    const { offsetX, offsetY } = event.native;
    const x = chart.scales.x.getValueForPixel(offsetX);
    const y = chart.scales.y.getValueForPixel(offsetY);
    // Check if click is near a point
    let minDist = Infinity, minIdx = -1;
    points.forEach((p, i) => {
      const dx = chart.scales.x.getPixelForValue(p.lux) - offsetX;
      const dy = chart.scales.y.getPixelForValue(p.intensity) - offsetY;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < minDist) { minDist = dist; minIdx = i; }
    });
    if (minDist < 12) { // Remove point if click is close
      setPoints(points.filter((_, i) => i !== minIdx)); // Do not sort or mutate others
    } else {
      // Add point if not too close to existing
      if (points.some(p => Math.abs(p.lux - x) < 1e-6)) return;
      setPoints([...points, { lux: x, intensity: y }].sort((a, b) => a.lux - b.lux));
    }
  };

  // Generate piecewise linear curve (no smoothing, just the control points)
  const generatePiecewiseLinearCurve = useCallback(() => {
    if (!points.length) return [];
    // Sort points by lux for the line, but do not mutate the original array
    return [...points].sort((a, b) => a.lux - b.lux);
  }, [points]);

  const chartData = {
    datasets: [
      {
        label: 'Intensity Curve',
        data: generatePiecewiseLinearCurve().map(p => ({ x: p.lux, y: p.intensity })),
        borderColor: 'rgb(75, 192, 192)',
        tension: 0,
        pointRadius: 0,
        fill: false,
        showLine: true,
        order: 1,
      },
      {
        label: 'Control Points',
        data: points.map(p => ({ x: p.lux, y: p.intensity })),
        backgroundColor: 'rgb(255, 99, 132)',
        pointRadius: 6,
        showLine: false,
        dragData: true,
        dragDataRound: 2,
        order: 2,
      },
    ],
  };

  // Drag-and-drop handler: update the dragged point, do not sort others
  const handleDrag = (e: any, datasetIndex: number, index: number, value: { x: number, y: number }) => {
    if (datasetIndex !== 1) return; // Only allow dragging control points
    let newPoints = [...points];
    // Clamp values and prevent duplicate lux (except for the current point)
    value.x = Math.max(0, value.x);
    value.y = Math.max(0, Math.min(1, value.y));
    if (newPoints.some((p, i) => i !== index && Math.abs(p.lux - value.x) < 1e-6)) return;
    newPoints[index] = { lux: value.x, intensity: value.y };
    setPoints(newPoints);
  };

  const chartOptions = {
    responsive: true,
    plugins: {
      legend: { display: true },
      dragData: {
        showTooltip: true,
        onDragEnd: handleDrag,
        // Allow both X and Y dragging
        dragX: true,
        dragY: true,
      },
    },
    onClick: handleChartClick,
    scales: {
      x: {
        type: 'linear' as const,
        title: { display: true, text: 'Lux' },
      },
      y: {
        type: 'linear' as const,
        title: { display: true, text: 'Intensity' },
        min: 0,
        max: 1,
      },
    },
  };

  return (
    <div className="container mx-auto p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold mb-4">Light Sensor Settings</h1>
        <div className="flex gap-4 mb-4">
          <Select
            value={selectedSensor}
            onValueChange={setSelectedSensor}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="Select sensor" />
            </SelectTrigger>
            <SelectContent>
              {Object.keys(settings?.sensor_mappings || {}).map((sensor) => (
                <SelectItem key={sensor} value={sensor}>
                  {sensor}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Target group"
            value={targetGroup}
            onChange={(e) => setTargetGroup(e.target.value)}
            className="w-[200px]"
          />
          <Button onClick={handleSave} disabled={!isEditing}>
            Save Changes
          </Button>
          <Button onClick={() => setIsEditing(!isEditing)} variant="outline">
            {isEditing ? 'Cancel' : 'Edit'}
          </Button>
        </div>
      </div>

      {/* Sensor Data Readout */}
      <div className="mb-8">
        <h2 className="text-xl font-bold mb-4">Sensor Data</h2>
        {sensorData && Object.entries(sensorData.sensors).map(([sensorName, data]) => (
          <div key={sensorName} className="mb-8 p-4 border rounded-lg">
            <h3 className="text-lg font-semibold mb-2">{sensorName}</h3>
            <div className="text-lg mb-4">
              <div>Current Lux: {data.current.lux?.toFixed(2) ?? 'N/A'}</div>
              <div>Target Intensity: {data.current.target_intensity?.toFixed(2) ?? 'N/A'}</div>
              {data.current.target_group && (
                <div>Target Group: {data.current.target_group}</div>
              )}
            </div>
            <h4 className="font-semibold mb-2">History (last {data.history.length} entries)</h4>
            <div className="max-h-[300px] overflow-y-auto border rounded p-2">
              <ul className="font-mono text-sm">
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

      {/* Intensity Curve */}
      <div className="mb-8">
        <h2 className="text-xl font-bold mb-4">Intensity Curve</h2>
        <div className="bg-white p-4 rounded-lg shadow">
          <Line data={chartData} options={chartOptions} />
        </div>
      </div>

      {/* Control Points */}
      {isEditing && (
        <div className="mb-8">
          <h2 className="text-xl font-bold mb-4">Control Points</h2>
          <div className="space-y-4">
            {points.map((point, index) => (
              <div key={index} className="flex items-center gap-4">
                <Input
                  type="number"
                  value={point.lux}
                  onChange={(e) => handlePointChange(index, 'lux', parseFloat(e.target.value))}
                  placeholder="Lux"
                  className="w-[100px]"
                />
                <Input
                  type="number"
                  value={point.intensity}
                  onChange={(e) => handlePointChange(index, 'intensity', parseFloat(e.target.value))}
                  placeholder="Intensity"
                  className="w-[100px]"
                />
                <Button
                  onClick={() => handleDeletePoint(index)}
                  variant="destructive"
                  size="sm"
                >
                  Delete
                </Button>
              </div>
            ))}
            <Button onClick={handleAddPoint} variant="outline">
              Add Point
            </Button>
          </div>
        </div>
      )}
    </div>
  );
} 