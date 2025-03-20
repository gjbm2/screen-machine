
import React, { useState, useRef, useEffect } from 'react';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { Button } from '@/components/ui/button';
import { X, Maximize2, Minimize2, Copy, ChevronsDown, RefreshCw } from 'lucide-react';
import ConsoleOutput from './ConsoleOutput';
import apiService from '@/utils/api';
import { useQuery } from '@tanstack/react-query';

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
  const [size, setSize] = useState(25); // Default size in viewport height percentage
  const [isMaximized, setIsMaximized] = useState(false);
  const [copied, setCopied] = useState(false);
  const [isExiting, setIsExiting] = useState(false);
  const [combinedLogs, setCombinedLogs] = useState<string[]>(logs);
  const consoleRef = useRef<HTMLDivElement>(null);
  
  // Fetch backend logs using React Query
  const { data: backendLogsData, isLoading, refetch } = useQuery({
    queryKey: ['backendLogs'],
    queryFn: () => apiService.getLogs(100),
    refetchInterval: isVisible ? 5000 : false, // Refresh every 5 seconds when visible
    enabled: isVisible,
  });
  
  useEffect(() => {
    if (isVisible) {
      setIsExiting(false);
      refetch();
    }
  }, [isVisible, refetch]);
  
  // Combine frontend and backend logs
  useEffect(() => {
    if (backendLogsData?.logs) {
      // Create a combined and sorted log array
      const backendLogs = backendLogsData.logs;
      const allLogs = [...logs, ...backendLogs];
      setCombinedLogs(allLogs);
    } else {
      setCombinedLogs(logs);
    }
  }, [logs, backendLogsData]);
  
  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [combinedLogs]);
  
  const handleToggleMaximize = () => {
    setIsMaximized(prev => !prev);
    setSize(prev => prev === 60 ? 25 : 60);
  };
  
  const handleCopyLogs = () => {
    navigator.clipboard.writeText(combinedLogs.join('\n'));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  
  const handleClose = () => {
    setIsExiting(true);
    setTimeout(() => {
      onClose();
    }, 300); // Wait for animation to complete
  };
  
  const handleRefreshLogs = () => {
    refetch();
  };
  
  if (!isVisible && !isExiting) {
    return null;
  }
  
  return (
    <div 
      className={`fixed bottom-0 left-0 right-0 z-50 border-t border-border ${isExiting ? 'animate-slide-out-down' : 'animate-slide-in-up'}`}
      style={{ 
        height: `${size}vh`,
        transition: 'height 0.3s ease'
      }}
    >
      <div className="absolute left-0 right-0 -top-3 flex justify-center">
        <div className="h-3 w-16 bg-background border-t border-x border-border rounded-t-md cursor-ns-resize flex justify-center items-center hover:bg-primary/10"
             onMouseDown={(e) => {
               document.querySelector('.rs-handle')?.dispatchEvent(
                 new MouseEvent('mousedown', {
                   bubbles: true,
                   clientX: e.clientX,
                   clientY: e.clientY
                 })
               );
             }}
        >
          <div className="w-8 h-1 bg-border rounded-full"></div>
        </div>
      </div>
      
      <ResizablePanelGroup direction="vertical">
        <ResizablePanel 
          defaultSize={100} 
          onResize={setSize}
          minSize={15}
          maxSize={60}
          className="bg-background"
        >
          <div className="h-full flex flex-col">
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <h3 className="text-sm font-medium">Console Output</h3>
              <div className="flex items-center space-x-1">
                <Button 
                  variant="ghost" 
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleRefreshLogs}
                  disabled={isLoading}
                >
                  <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                  <span className="sr-only">Refresh logs</span>
                </Button>
                <Button 
                  variant="ghost" 
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleCopyLogs}
                >
                  <Copy className="h-4 w-4" />
                  <span className="sr-only">Copy logs</span>
                </Button>
                <Button 
                  variant="ghost" 
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleToggleMaximize}
                >
                  {isMaximized ? 
                    <Minimize2 className="h-4 w-4" /> : 
                    <Maximize2 className="h-4 w-4" />
                  }
                  <span className="sr-only">
                    {isMaximized ? "Minimize console" : "Maximize console"}
                  </span>
                </Button>
                <Button 
                  variant="ghost" 
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleClose}
                >
                  <X className="h-4 w-4" />
                  <span className="sr-only">Close console</span>
                </Button>
              </div>
            </div>
            <div ref={consoleRef} className="flex-1 overflow-auto h-full">
              <ConsoleOutput logs={combinedLogs} />
            </div>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle className="cursor-ns-resize hover:bg-primary/10" />
      </ResizablePanelGroup>
    </div>
  );
};

export default ResizableConsole;
