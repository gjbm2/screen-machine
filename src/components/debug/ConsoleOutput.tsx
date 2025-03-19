
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
  
  return (
    <div 
      ref={consoleRef}
      className="p-3 overflow-auto font-mono text-xs bg-black text-white h-full"
    >
      {logs.length === 0 ? (
        <p className="text-white/60">No console logs yet.</p>
      ) : (
        logs.map((log, index) => (
          <div key={index} className="py-1 border-b border-white/10 last:border-0">
            {log}
          </div>
        ))
      )}
    </div>
  );
};

export default ConsoleOutput;
