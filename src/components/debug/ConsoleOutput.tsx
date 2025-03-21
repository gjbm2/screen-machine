import React, { useRef, useEffect, useMemo } from 'react';

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
  
  // Sort logs by timestamp (if they start with timestamp format)
  const sortedLogs = useMemo(() => {
    return [...logs].sort((a, b) => {
      // Extract timestamp if log has format "[HH:MM:SS] message"
      const timeRegex = /\[(\d{1,2}:\d{1,2}:\d{1,2})\]/;
      const timeA = a.match(timeRegex)?.[1];
      const timeB = b.match(timeRegex)?.[1];
      
      if (timeA && timeB) {
        return timeA.localeCompare(timeB);
      }
      
      // If we can't parse timestamps, keep original order
      return 0;
    });
  }, [logs]);
  
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
