
import React from 'react';
import ResizableConsole from '@/components/debug/ResizableConsole';

interface ConsoleViewProps {
  logs: any[];
  onClose: () => void;
  onClear?: () => void;
}

const ConsoleView: React.FC<ConsoleViewProps> = ({ 
  logs,
  onClose,
  onClear = () => {}
}) => {
  return (
    <ResizableConsole 
      logs={logs} 
      isVisible={true}
      onClose={onClose}
      onClear={onClear}
    />
  );
};

export default ConsoleView;
