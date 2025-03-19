
import React, { useRef, useEffect, useState } from 'react';
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels';
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
  // Default console size is 30% of viewport height
  const [size, setSize] = useState(30);
  
  const handleResize = (sizes: number[]) => {
    if (sizes[0]) {
      setSize(sizes[0]);
    }
  };

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
              <div className="w-12 h-1 bg-muted-foreground/30 rounded-full absolute left-1/2 top-2 -translate-x-1/2 cursor-ns-resize" />
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
        </Panel>
        <PanelResizeHandle 
          className="h-1.5 bg-muted hover:bg-primary/20 cursor-ns-resize transition-colors"
        />
      </PanelGroup>
    </div>
  );
};

export default ResizableConsole;
