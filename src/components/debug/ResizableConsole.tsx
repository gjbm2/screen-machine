
import React, { useRef, useEffect, useState } from 'react';
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels';
import ConsoleOutput from './ConsoleOutput';
import { Button } from '../ui/button';
import { X } from 'lucide-react';

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
  // Increase the default size from 20 to 40 percent
  const [size, setSize] = useState(40);
  
  const handleResize = (sizes: number[]) => {
    if (sizes[0]) {
      setSize(sizes[0]);
    }
  };
  
  return (
    <div 
      className={`fixed bottom-0 left-0 right-0 bg-background border-t border-border z-50 transition-all duration-300 ease-in-out ${
        isVisible ? 'translate-y-0' : 'translate-y-full'
      }`}
      style={{ height: isVisible ? `${size}vh` : '0' }}
    >
      <PanelGroup direction="vertical" onLayout={handleResize}>
        <Panel 
          defaultSize={size} 
          minSize={10}
          className="overflow-hidden"
        >
          <div className="h-full flex flex-col">
            <div className="flex justify-between items-center p-2 border-b">
              <h3 className="text-sm font-medium">Console Output</h3>
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={onClose}
                className="h-7 w-7 p-0"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex-grow overflow-auto">
              <ConsoleOutput 
                logs={logs} 
                isVisible={isVisible} 
                onClose={onClose} 
              />
            </div>
          </div>
        </Panel>
        <PanelResizeHandle 
          className="h-1.5 bg-muted hover:bg-primary/20 cursor-ns-resize transition-colors"
        />
      </PanelGroup>
    </div>
  );
};

export default ResizableConsole;
