
import React, { useRef, useEffect, useState } from 'react';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import ConsoleOutput from './ConsoleOutput';
import { Button } from '../ui/button';
import { X, Save } from 'lucide-react';

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
  // Use state to control the panel size (default to 30% of viewport height)
  const [size, setSize] = useState(30);
  const resizingRef = useRef(false);
  const startPosRef = useRef(0);
  const startSizeRef = useRef(0);
  
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
    } catch (error) {
      console.error('Error saving logs:', error);
    }
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    resizingRef.current = true;
    startPosRef.current = e.clientY;
    startSizeRef.current = size;
    
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'ns-resize';
  };
  
  const handleMouseMove = (e: MouseEvent) => {
    if (!resizingRef.current) return;
    
    // Calculate the difference in mouse position
    const diff = startPosRef.current - e.clientY;
    
    // Convert the difference to vh units (without any delay)
    const diffVh = (diff / window.innerHeight) * 100;
    
    // Calculate the new size (percentage of viewport height)
    const newSize = Math.max(10, Math.min(70, startSizeRef.current + diffVh));
    
    // Use requestAnimationFrame for smoother updates
    requestAnimationFrame(() => {
      setSize(newSize);
    });
  };
  
  const handleMouseUp = () => {
    resizingRef.current = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = '';
  };
  
  // Cleanup event listeners on unmount
  useEffect(() => {
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
    };
  }, []);
  
  return (
    <div 
      className={`fixed bottom-0 left-0 right-0 bg-background border-t border-border z-50 transition-all duration-300 ease-in-out ${
        isVisible ? 'translate-y-0' : 'translate-y-full'
      }`}
      style={{ height: isVisible ? `${size}vh` : '0' }}
    >
      <div className="h-full flex flex-col">
        <div className="flex justify-between items-center p-2 border-b">
          <div 
            className="w-12 h-1 bg-muted-foreground/30 rounded-full absolute left-1/2 top-2 -translate-x-1/2 cursor-ns-resize"
            onMouseDown={handleMouseDown}
          ></div>
          <h3 className="text-sm font-medium ml-2">Console Output</h3>
          <div className="flex items-center space-x-2">
            <Button 
              variant="outline" 
              size="sm" 
              className="h-7 text-xs"
              onClick={handleSaveLogs}
            >
              <Save className="h-3 w-3 mr-1" /> Save Logs
            </Button>
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={onClose}
              className="h-7 w-7 p-0"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <div className="flex-grow overflow-auto">
          <ConsoleOutput 
            logs={logs} 
            isVisible={isVisible} 
            onClose={onClose} 
          />
        </div>
      </div>
    </div>
  );
};

export default ResizableConsole;
