
import React, { useState, useEffect, useRef } from 'react';
import { X, Maximize, Minimize, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ResizablePanel, ResizablePanelGroup, ResizableHandle } from '@/components/ui/resizable';
import { toast } from 'sonner';

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

  const handleSaveLogs = () => {
    try {
      const logContent = logs.join('\n');
      const blob = new Blob([logContent], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      
      const a = document.createElement('a');
      a.href = url;
      a.download = `console-logs-${new Date().toISOString().split('T')[0]}.txt`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      
      toast.success('Console logs saved');
    } catch (error) {
      console.error('Error saving logs:', error);
      toast.error('Failed to save logs');
    }
  };

  if (!isVisible) return null;

  return (
    <div 
      className={`fixed ${isFullscreen ? 'inset-0' : 'bottom-0 left-0 right-0'} bg-black text-white z-50 transition-all`}
    >
      <ResizablePanelGroup
        direction="vertical"
        className={`h-${isFullscreen ? 'screen' : '64'}`}
      >
        <ResizablePanel defaultSize={100} minSize={10}>
          <div className="flex items-center justify-between p-3 border-b border-gray-700">
            <h3 className="text-sm font-semibold">Console Output</h3>
            <div className="flex gap-2">
              <Button 
                variant="ghost" 
                size="icon"
                className="h-6 w-6 text-gray-400 hover:text-white hover:bg-gray-800"
                onClick={handleSaveLogs}
                title="Save logs"
              >
                <Save className="h-4 w-4" />
              </Button>
              <Button 
                variant="ghost" 
                size="icon"
                className="h-6 w-6 text-gray-400 hover:text-white hover:bg-gray-800"
                onClick={() => setIsFullscreen(!isFullscreen)}
                title={isFullscreen ? "Minimize" : "Maximize"}
              >
                {isFullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
              </Button>
              <Button 
                variant="ghost" 
                size="icon"
                className="h-6 w-6 text-gray-400 hover:text-white hover:bg-gray-800"
                onClick={onClose}
                title="Close"
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
        </ResizablePanel>
        <ResizableHandle withHandle />
      </ResizablePanelGroup>
    </div>
  );
};

export default ConsoleOutput;
