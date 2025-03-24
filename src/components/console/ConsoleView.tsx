
import React from 'react';

interface ConsoleViewProps {
  logs: any[];
  onClear: () => void;
}

const ConsoleView: React.FC<ConsoleViewProps> = ({ logs, onClear }) => {
  return (
    <div className="h-full overflow-auto bg-black text-green-400 font-mono text-sm p-2">
      <div className="flex justify-between items-center mb-2">
        <h3 className="text-white">Console Output</h3>
        <button 
          onClick={onClear}
          className="text-white hover:text-green-400 text-xs"
        >
          Clear
        </button>
      </div>
      <div className="space-y-1">
        {logs.map((log, index) => (
          <div key={index} className="break-all">
            {typeof log === 'string' ? log : JSON.stringify(log, null, 2)}
          </div>
        ))}
      </div>
    </div>
  );
};

export default ConsoleView;
