import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import Header from '@/components/Header';
import { Button } from '@/components/ui/button';
import { Terminal, Menu, Info, StopCircle, SlidersHorizontal } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { toast } from 'sonner';
import { generationApi } from '@/shared/api';
import { useRoomLink, liveStateOf, roomPageUrl } from '@/components/room/roomLink';
import { AppMenu } from '@/components/nav/AppMenu';

interface HeaderSectionProps {
  onToggleConsole: () => void;
  isConsoleVisible: boolean;
  onOpenAdvancedOptions?: () => void;
  onOpenAboutDialog?: () => void;
}

// The generator's own menu actions, under "On this page" in the shared menu.
const PAGE_ACTION =
  'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-base font-medium hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
const PAGE_ICON = 'grid h-9 w-9 shrink-0 place-items-center rounded-full bg-stone-100 text-stone-600';

const HeaderSection: React.FC<HeaderSectionProps> = ({ 
  onToggleConsole,
  isConsoleVisible,
  onOpenAboutDialog
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  // This header is shared by the generator (/) and the scheduler (/scheduler).
  const current = useLocation().pathname.startsWith('/scheduler') ? 'scheduler' : 'generator';
  const room = useRoomLink();
  const liveCount = Object.values(room.status?.screens ?? {}).filter(s => liveStateOf(s) === 'live').length;

  const handleCancelAllJobs = async () => {
    toast.loading("Cancelling all jobs...");
    try {
      const result = await generationApi.cancelAllJobs();
      if (result.success) {
        toast.success(`Cancelled ${result.cancelled} jobs`);
      } else {
        toast.error(`Failed to cancel jobs: ${result.error}`);
      }
    } catch (error) {
      console.error("Error cancelling jobs:", error);
      toast.error("Failed to cancel jobs due to an unexpected error");
    }
  };

  return (
    <div className="flex justify-between items-center">
      <Header onOpenAboutDialog={onOpenAboutDialog} />
      
      <div className="flex items-center space-x-1">
        {/* Room console: up front whenever the room's LAN door answers */}
        {room.onLan && (
          <Button
            asChild
            size="sm"
            className="h-10 rounded-full px-3 sm:px-4 mr-1 bg-teal-700 text-white hover:bg-teal-800"
          >
            <a href={roomPageUrl()} data-testid="home-room-button">
              <SlidersHorizontal className="h-4 w-4" />
              <span className="ml-1.5">Room</span>
              {liveCount > 0 && (
                <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-red-600 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide">
                  <span className="h-1.5 w-1.5 rounded-full bg-white motion-safe:animate-pulse" />
                  {liveCount} live
                </span>
              )}
            </a>
          </Button>
        )}

        {/* Only show console button outside the menu if visible */}
        {isConsoleVisible && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon" 
                onClick={onToggleConsole}
                className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50 mr-2"
              >
                <Terminal className="h-5 w-5 text-primary" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Toggle Command Console</p>
            </TooltipContent>
          </Tooltip>
        )}
        
        {/* The shared menu (same as the Room console's): Go to, Screens, this page, Developer */}
        <Button 
          variant="ghost" 
          size="icon" 
          onClick={() => setMenuOpen(true)}
          aria-label="Menu"
          data-testid="menu-button"
          className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current={current} pageTitle="On this page">
          <div className="space-y-1">
            <button type="button" className={PAGE_ACTION} onClick={() => { setMenuOpen(false); void handleCancelAllJobs(); }}>
              <span className={PAGE_ICON}><StopCircle className="h-5 w-5" /></span>
              Cancel all jobs
            </button>
            <button type="button" className={PAGE_ACTION} onClick={() => { setMenuOpen(false); onToggleConsole(); }}>
              <span className={PAGE_ICON}><Terminal className="h-5 w-5" /></span>
              {isConsoleVisible ? 'Hide the console' : 'Show the console'}
            </button>
            <button type="button" className={PAGE_ACTION} onClick={() => { setMenuOpen(false); onOpenAboutDialog?.(); }}>
              <span className={PAGE_ICON}><Info className="h-5 w-5" /></span>
              About
            </button>
          </div>
        </AppMenu>
      </div>
    </div>
  );
};

export default HeaderSection;
