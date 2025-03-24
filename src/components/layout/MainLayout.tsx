
import React from 'react';
import Header from '@/components/Header';
import Footer from '@/components/Footer';
import ResizableConsole from '@/components/debug/ResizableConsole';
import ConsoleView from '@/components/console/ConsoleView';
import { useVerboseDebug } from '@/hooks/use-verbose-debug';

interface MainLayoutProps {
  children: React.ReactNode;
  onToggleConsole: () => void;
  consoleVisible: boolean;
  onOpenAdvancedOptions: () => void;
  consoleLogs: any[];
  onClearConsole: () => void;
  isFirstRun?: boolean;
}

const MainLayout: React.FC<MainLayoutProps> = ({
  children,
  onToggleConsole,
  consoleVisible,
  onOpenAdvancedOptions,
  consoleLogs,
  onClearConsole,
  isFirstRun = false
}) => {
  const { verboseDebug, toggleVerboseDebug } = useVerboseDebug();

  return (
    <div className="flex flex-col min-h-screen">
      <Header 
        onToggleConsole={onToggleConsole} 
        onOpenAdvancedOptions={onOpenAdvancedOptions}
        onToggleVerboseDebug={toggleVerboseDebug}
        verboseDebugEnabled={verboseDebug}
      />
      
      <main className="flex-1 container mx-auto px-4 py-4">
        {children}
      </main>
      
      <Footer />
      
      {consoleVisible && (
        <ResizableConsole
          logs={consoleLogs}
          isVisible={consoleVisible}
          onClose={onToggleConsole}
          onClear={onClearConsole}
        />
      )}
    </div>
  );
};

export default MainLayout;
