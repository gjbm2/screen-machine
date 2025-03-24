
import React from 'react';
import { Trash } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';

interface ConsoleViewProps {
  logs: any[];
  onClear: () => void;
}

const ConsoleView: React.FC<ConsoleViewProps> = ({ logs, onClear }) => {
  return (
    <div className="flex flex-col h-full">
      <div className="p-2 flex justify-between items-center border-b">
        <h3 className="text-sm font-medium">Console Output</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          className="h-8 px-2 text-muted-foreground"
        >
          <Trash className="h-4 w-4 mr-1" />
          Clear
        </Button>
      </div>
      
      <ScrollArea className="flex-1 p-2">
        <div className="space-y-2">
          {logs.map((log, index) => (
            <div 
              key={index} 
              className={`text-xs p-2 rounded ${getLogTypeClass(log.type)}`}
            >
              <div className="font-medium">{log.message}</div>
              {log.details && (
                <div className="mt-1 text-xs opacity-80 overflow-x-auto">
                  <pre>{formatDetails(log.details)}</pre>
                </div>
              )}
            </div>
          ))}
          
          {logs.length === 0 && (
            <div className="text-xs text-muted-foreground py-4 text-center italic">
              No logs to display
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
};

// Helper function to get CSS class based on log type
function getLogTypeClass(type: string): string {
  switch (type) {
    case 'error':
      return 'bg-red-50 text-red-800 border border-red-200';
    case 'warning':
      return 'bg-amber-50 text-amber-800 border border-amber-200';
    case 'success':
      return 'bg-green-50 text-green-800 border border-green-200';
    case 'info':
    default:
      return 'bg-blue-50 text-blue-800 border border-blue-200';
  }
}

// Helper function to format details object
function formatDetails(details: any): string {
  if (typeof details === 'string') return details;
  
  try {
    return JSON.stringify(details, null, 2);
  } catch (error) {
    return String(details);
  }
}

export default ConsoleView;
