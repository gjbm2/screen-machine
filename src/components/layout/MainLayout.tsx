
import React from 'react';
import HeaderSection from '@/components/main/HeaderSection';
import ConsoleView from '@/components/console/ConsoleView';

interface MainLayoutProps {
  children: React.ReactNode;
  onToggleConsole: () => void;
  consoleVisible?: boolean;
  onOpenAdvancedOptions?: () => void;
  consoleLogs?: any[];
  onClearConsole?: () => void;
  isFirstRun?: boolean;
  isVerboseDebug?: boolean;
  onToggleVerboseDebug?: () => void;
}

const MainLayout: React.FC<MainLayoutProps> = ({
  children,
  onToggleConsole,
  consoleVisible = false,
  onOpenAdvancedOptions,
  consoleLogs = [],
  onClearConsole,
  isFirstRun = false,
  isVerboseDebug = false,
  onToggleVerboseDebug
}) => {
  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <div className="container mx-auto px-4 py-2 md:py-4 flex-1 flex flex-col">
        <HeaderSection 
          onToggleConsole={onToggleConsole}
          isConsoleVisible={consoleVisible}
          onOpenAdvancedOptions={onOpenAdvancedOptions}
          isVerboseDebug={isVerboseDebug}
          onToggleVerboseDebug={onToggleVerboseDebug}
        />
        
        <div className={`flex-1 w-full flex flex-col ${isFirstRun ? 'justify-center' : ''}`}>
          <div className="flex-1 py-4">
            {/* Main content */}
            {children}
          </div>
        </div>
      </div>
      
      {/* Console View */}
      {consoleVisible && (
        <ConsoleView 
          logs={consoleLogs} 
          onClose={onToggleConsole}
          onClear={onClearConsole}
        />
      )}
    </div>
  );
};

export default MainLayout;
