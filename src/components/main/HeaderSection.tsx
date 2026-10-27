import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Terminal, Info, StopCircle, ListChecks } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cancelEveryJob } from '@/components/jobs/jobWords';
import { AppLink } from '@/components/nav/AppLink';
import { jobsUrl } from '@/components/nav/appNav';
import { AppMenu } from '@/components/nav/AppMenu';
import { PageHeader } from '@/components/nav/PageHeader';
import { RoomThemeStyle, THEME_CLASS } from '@/pages/Room/theme';

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

  // The app's own header (PageHeader), as on Room, Music and Lights (owner, 27 Oct: "shouldn't generator also show the
  // same top nav apparatus?"): the room's mode, Mic and AI, and the menu; on an iPad or wider they sit in the app's top
  // bar, on a phone beside the page's title. The tab bar has Room, so no Room button here.
  return (
    <div className={THEME_CLASS}>
      <RoomThemeStyle />
      <PageHeader
        title="Generator"
        quiet
        current={current}
        onMenu={() => setMenuOpen(true)}
        toggles="auto"
        actions={
          isConsoleVisible && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" onClick={onToggleConsole} aria-label="Toggle Command Console" className="h-11 w-11 shrink-0 rounded-full">
                  <Terminal className="h-5 w-5 text-primary" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Toggle Command Console</p>
              </TooltipContent>
            </Tooltip>
          )
        }
      />
      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current={current} pageTitle="On this page">
        <div className="space-y-1">
          <AppLink href={jobsUrl()} className={PAGE_ACTION} onClick={() => setMenuOpen(false)}>
            <span className={PAGE_ICON}><ListChecks className="h-5 w-5" /></span>
            Jobs
          </AppLink>
          <button type="button" className={PAGE_ACTION} onClick={() => { setMenuOpen(false); void cancelEveryJob(); }}>
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
  );
};

export default HeaderSection;
