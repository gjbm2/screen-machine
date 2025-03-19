
import React, { useRef, useEffect } from 'react';
import { Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

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
  
  const handleSaveLogs = () => {
    try {
      const blob = new Blob([logs.join('\n')], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `console-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success('Console logs saved successfully');
    } catch (error) {
      console.error('Error saving logs:', error);
      toast.error('Failed to save console logs');
    }
  };
  
  return (
    <div className="flex flex-col h-full">
      <div className="flex justify-between items-center py-1 px-2 bg-black border-b border-white/10">
        <div className="text-xs text-white/70">Console Output</div>
        <Button 
          variant="ghost" 
          size="sm" 
          className="h-7 w-7 p-0 text-white/70 hover:text-white hover:bg-white/10"
          onClick={handleSaveLogs}
        >
          <Save className="h-4 w-4" />
        </Button>
      </div>
      <div 
        ref={consoleRef}
        className="p-3 overflow-auto font-mono text-xs bg-black text-white flex-1"
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
    </div>
  );
};

export default ConsoleOutput;
