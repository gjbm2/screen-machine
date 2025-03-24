
import React, { useState, useCallback } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import HeaderSection from '@/components/main/HeaderSection';
import ResizableConsole from '@/components/debug/ResizableConsole';
import AboutDialog from '@/components/about/AboutDialog';
import Footer from '@/components/Footer';
import { useConsoleManagement } from '@/hooks/use-console-management';

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
  const [showAboutDialog, setShowAboutDialog] = useState(false);
  
  return (
    <main className="flex flex-col min-h-screen p-4 md:p-6 max-w-screen-2xl mx-auto">
      <HeaderSection 
        onToggleConsole={onToggleConsole}
        isConsoleVisible={consoleVisible}
        onOpenAdvancedOptions={onOpenAdvancedOptions}
        onOpenAboutDialog={() => setShowAboutDialog(true)}
      />
      
      <ScrollArea className="flex-1 max-h-full overflow-y-auto pr-4">
        {children}
        <Footer />
      </ScrollArea>
      
      {consoleVisible && (
        <ResizableConsole 
          logs={consoleLogs}
          isVisible={consoleVisible}
          onClose={onToggleConsole}
          onClear={onClearConsole}
        />
      )}
      
      <AboutDialog 
        open={showAboutDialog} 
        onOpenChange={setShowAboutDialog}
      />
    </main>
  );
};

export default MainLayout;
