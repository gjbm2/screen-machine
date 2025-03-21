
import React, { useRef, useEffect } from 'react';

interface ConsoleOutputProps {
  logs: string[];
}

const ConsoleOutput: React.FC<ConsoleOutputProps> = ({ logs }) => {
  const consoleRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [logs]);

  // Sort logs by timestamp if present
  const sortedLogs = [...logs].sort((a, b) => {
    // Extract timestamp from log entries if they follow the [HH:MM:SS] format
    const getTimestamp = (log: string) => {
      const match = log.match(/\[(\d{1,2}:\d{2}:\d{2})\]/);
      if (match && match[1]) {
        const timeParts = match[1].split(':').map(Number);
        // Convert to seconds for comparison
        return timeParts[0] * 3600 + timeParts[1] * 60 + timeParts[2];
      }
      return 0; // Default value if no timestamp found
    };
    
    return getTimestamp(a) - getTimestamp(b);
  });
  
  return (
    <div 
      ref={consoleRef}
      className="h-full font-mono text-xs bg-black text-white overflow-y-auto p-2 w-full"
    >
      {sortedLogs.length === 0 ? (
        <p className="text-white/60 p-2">No console logs yet.</p>
      ) : (
        sortedLogs.map((log, index) => (
          <div key={index} className="py-1 border-b border-white/10 last:border-0">
            {log}
          </div>
        ))
      )}
    </div>
  );
};

export default ConsoleOutput;
