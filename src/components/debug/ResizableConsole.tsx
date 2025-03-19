
import React, { useState, useRef, useEffect } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';

interface ResizableConsoleProps {
  logs: string[];
  isVisible: boolean;
  onClose: () => void;
}

const ResizableConsole: React.FC<ResizableConsoleProps> = ({ logs, isVisible, onClose }) => {
  const [height, setHeight] = useState(300);
  const contentRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (contentRef.current && isVisible) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [logs, isVisible]);
  
  if (!isVisible) return null;
  
  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-card border-t border-border shadow-lg">
      <ResizablePanelGroup direction="vertical">
        <ResizablePanel defaultSize={20} minSize={15} maxSize={80}>
          <div className="flex justify-between items-center p-2 border-b">
            <div className="font-mono text-sm">Console Output</div>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          
          <div 
            ref={contentRef}
            className="console-content h-64 p-3 overflow-auto text-sm font-mono whitespace-pre-wrap"
          >
            {logs.length === 0 ? (
              <div className="text-muted-foreground italic">No logs yet</div>
            ) : (
              logs.map((log, index) => (
                <div key={index} className="mb-1">
                  {log}
                </div>
              ))
            )}
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />
      </ResizablePanelGroup>
    </div>
  );
};

export default ResizableConsole;
