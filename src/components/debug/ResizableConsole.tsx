
import React, { useState, useEffect, useRef } from 'react';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { X, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import ConsoleOutput from './ConsoleOutput';
import { toast } from 'sonner';

interface ResizableConsoleProps {
  logs: string[];
  isVisible: boolean;
  onClose: () => void;
}

const ResizableConsole: React.FC<ResizableConsoleProps> = ({
  logs,
  isVisible,
  onClose
}) => {
  const [size, setSize] = useState<number>(25);
  const consoleRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (isVisible && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [logs, isVisible]);
  
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
  
  if (!isVisible) {
    return null;
  }
  
  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-background border-t border-border">
      <ResizablePanelGroup direction="vertical">
        <ResizablePanel 
          defaultSize={size} 
          onResize={setSize} 
          minSize={15}
          maxSize={60}
          className="h-auto"
        >
          <div className="h-full flex flex-col">
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <h3 className="text-sm font-medium">Console Output</h3>
              <div className="flex items-center space-x-1">
                <Button 
                  variant="ghost" 
                  size="icon" 
                  onClick={handleSaveLogs} 
                  className="h-6 w-6"
                  title="Save logs"
                >
                  <Save className="h-4 w-4" />
                </Button>
                <Button 
                  variant="ghost" 
                  size="icon" 
                  onClick={onClose} 
                  className="h-6 w-6"
                  title="Close console"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div ref={consoleRef} className="flex-1 overflow-y-auto console-content p-4 text-xs">
              <ConsoleOutput logs={logs} />
            </div>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />
      </ResizablePanelGroup>
    </div>
  );
};

export default ResizableConsole;
