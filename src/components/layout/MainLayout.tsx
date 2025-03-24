
import React, { ReactNode, useState } from 'react';
import Header from '@/components/Header';
import Footer from '@/components/Footer';
import { Menu, Terminal, X } from 'lucide-react';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import AboutDialog from '@/components/about/AboutDialog';
import ConsoleOutput from '@/components/debug/ConsoleOutput';
import ResizableConsole from '@/components/debug/ResizableConsole';
import { useIsMobile } from '@/hooks/use-mobile';

interface MainLayoutProps {
  children: ReactNode;
  onToggleConsole?: () => void;
  consoleVisible?: boolean;
  onOpenAdvancedOptions?: () => void;
  consoleLogs?: any[];
  onClearConsole?: () => void;
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
  const [isAboutOpen, setIsAboutOpen] = useState(false);
  const isMobile = useIsMobile();
  
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      
      <div className="fixed top-4 right-4 z-50 flex items-center space-x-2">
        {/* Console Toggle Button */}
        <Button
          variant="outline"
          size="icon"
          className="h-10 w-10 rounded-full bg-white border shadow-md hover:bg-slate-100"
          onClick={onToggleConsole}
          title="Toggle Console"
        >
          <Terminal className="h-5 w-5" />
        </Button>
        
        {/* Mobile Menu */}
        <Sheet>
          <SheetTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              className="h-10 w-10 rounded-full bg-white border shadow-md hover:bg-slate-100"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </SheetTrigger>
          <SheetContent>
            <div className="flex flex-col h-full">
              <div className="px-1 py-4">
                <h2 className="text-xl font-bold mb-2">Menu</h2>
                <Separator className="my-4" />
              </div>
              
              <div className="flex-1">
                <div className="space-y-4">
                  <Button 
                    variant="ghost" 
                    className="w-full justify-start" 
                    onClick={onOpenAdvancedOptions}
                  >
                    Advanced Options
                  </Button>
                  
                  <Button 
                    variant="ghost" 
                    className="w-full justify-start"
                    onClick={() => setIsAboutOpen(true)}
                  >
                    About
                  </Button>
                </div>
              </div>
            </div>
          </SheetContent>
        </Sheet>
      </div>
      
      <main className="flex-1 container mx-auto px-4 py-8">
        {children}
      </main>
      
      <Footer />
      
      {/* Console Output */}
      {consoleVisible && consoleLogs && (
        <ResizableConsole onClose={onToggleConsole} onClear={onClearConsole}>
          <ConsoleOutput logs={consoleLogs} />
        </ResizableConsole>
      )}
      
      {/* About Dialog */}
      <AboutDialog 
        isOpen={isAboutOpen}
        onOpenChange={setIsAboutOpen}
      />
    </div>
  );
};

export default MainLayout;
