
import React, { useEffect, useState } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import HeaderSection from '@/components/main/HeaderSection';
import ResizableConsole from '@/components/debug/ResizableConsole';
import AboutDialog from '@/components/about/AboutDialog';
import Footer from '@/components/Footer';
import { RoomThemeStyle, THEME_CLASS } from '@/pages/Room/theme';

interface MainLayoutProps {
  children: React.ReactNode;
  onToggleConsole: () => void;
  consoleVisible: boolean;
  onOpenAdvancedOptions: () => void;
  consoleLogs: unknown[];
  onClearConsole: () => void;
  isFirstRun?: boolean;
  /** The page's live-updates socket, shown as the header's status (the generator). */
  wsConnected?: boolean;
}

// The app's light, warm skin (the Room theme) on this page and on everything it opens in a portal (menus, sheets,
// dialogs, popovers), so the generator reads as the same app as Room and Music.
function useBodyTheme() {
  useEffect(() => {
    const body = document.body;
    const had = body.classList.contains(THEME_CLASS);
    body.classList.add(THEME_CLASS);
    return () => {
      if (!had) body.classList.remove(THEME_CLASS);
    };
  }, []);
}

const MainLayout: React.FC<MainLayoutProps> = ({
  children,
  onToggleConsole,
  consoleVisible,
  onOpenAdvancedOptions,
  consoleLogs,
  onClearConsole,
  wsConnected,
}) => {
  const [showAboutDialog, setShowAboutDialog] = useState(false);
  useBodyTheme();

  return (
    <main className={`${THEME_CLASS} flex flex-col min-h-screen bg-background px-4 pb-4 md:px-6 md:pb-6 max-w-screen-2xl mx-auto`}>
      <RoomThemeStyle />
      <HeaderSection
        onToggleConsole={onToggleConsole}
        isConsoleVisible={consoleVisible}
        onOpenAdvancedOptions={onOpenAdvancedOptions}
        onOpenAboutDialog={() => setShowAboutDialog(true)}
        wsConnected={wsConnected}
      />

      {/* the same 16 px gutter on both sides (no right padding of its own) */}
      <ScrollArea className="flex-1 max-h-full overflow-y-auto mt-2">
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
