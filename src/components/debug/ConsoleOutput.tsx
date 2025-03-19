
import React, { useState, useEffect, useRef } from 'react';
import { X, Maximize, Minimize } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';

interface ConsoleOutputProps {
  logs: string[];
  isVisible: boolean;
  onClose: () => void;
}

const ConsoleOutput: React.FC<ConsoleOutputProps> = ({ logs, isVisible, onClose }) => {
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Auto-scroll to bottom when new logs are added
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  if (!isVisible) return null;

  return (
    <div 
      className={`fixed ${isFullscreen ? 'inset-0' : 'bottom-0 left-0 right-0 h-64'} bg-black text-white z-50 transition-all`}
    >
      <div className="flex items-center justify-between p-3 border-b border-gray-700">
        <h3 className="text-sm font-semibold">Console Output</h3>
        <div className="flex gap-2">
          <Button 
            variant="ghost" 
            size="icon"
            className="h-6 w-6 text-gray-400 hover:text-white hover:bg-gray-800"
            onClick={() => setIsFullscreen(!isFullscreen)}
          >
            {isFullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
          </Button>
          <Button 
            variant="ghost" 
            size="icon"
            className="h-6 w-6 text-gray-400 hover:text-white hover:bg-gray-800"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      
      <ScrollArea className="h-[calc(100%-40px)]" ref={scrollRef}>
        <div className="p-3 font-mono text-xs whitespace-pre-wrap">
          {logs.length === 0 ? (
            <div className="text-gray-500 italic">No logs to display yet...</div>
          ) : (
            logs.map((log, index) => (
              <div key={index} className="mb-1">
                {log}
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
};

export default ConsoleOutput;
