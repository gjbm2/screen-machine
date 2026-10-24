// The generator's header (and, until Schedules has its own, the scheduler's): the app's one header (PageHeader) with
// the title, Mic and AI while the room answers, the live-updates link as the status pill (only when it isn't fine),
// the places row and the one menu (owner, 24 Oct: "you just haven't really worked the interface ui through properly
// throughout"). The menu is navigation and read-only status only: the console and About sit in its Developer fold;
// "Cancel all" lives on the generator's jobs strip, next to the jobs it cancels.

import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Info, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { roomPageUrl } from '@/components/room/roomLink';
import { AppMenu } from '@/components/nav/AppMenu';
import { PageHeader, type HeaderStatus } from '@/components/nav/PageHeader';

interface HeaderSectionProps {
  onToggleConsole: () => void;
  isConsoleVisible: boolean;
  onOpenAdvancedOptions?: () => void;
  onOpenAboutDialog?: () => void;
  /** The generator's live-updates socket (job progress, new pictures). Undefined: the page has none. */
  wsConnected?: boolean;
}

const DEV_ROW =
  'flex min-h-11 w-full items-center gap-3 rounded-xl px-3 py-1.5 text-left text-[15px] font-medium hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/** The screen tab the generator shows (?publish=<id>, kept in the address by the tabs). */
function shownScreen(): string | null {
  return new URLSearchParams(window.location.search).get('publish');
}

const HeaderSection: React.FC<HeaderSectionProps> = ({ onToggleConsole, isConsoleVisible, onOpenAboutDialog, wsConnected }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const isScheduler = useLocation().pathname.startsWith('/scheduler');
  const current = isScheduler ? 'scheduler' : 'generator';

  // The socket takes a moment on load: say nothing until it has had a fair chance.
  const [settling, setSettling] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setSettling(false), 4000);
    return () => clearTimeout(t);
  }, []);
  const status: HeaderStatus | null =
    wsConnected === undefined
      ? null
      : wsConnected
        ? { tone: 'ok', word: 'Connected' }
        : settling
          ? null
          : {
              tone: 'warn',
              word: 'Reconnecting',
              sentence: "The generator's live updates aren't connected. Pictures still get made; progress shows again once it reconnects.",
              pulse: true,
            };

  // The places row's Room opens the Room page on the screen whose tab is showing (?publish=<id>).
  const onClickCapture = (e: React.MouseEvent) => {
    if (isScheduler || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    const room = (e.target as HTMLElement).closest('[data-testid="place-room"]');
    const screen = shownScreen();
    if (!room || !screen) return;
    e.preventDefault();
    e.stopPropagation();
    navigate(roomPageUrl(screen));
  };

  return (
    <div onClickCapture={onClickCapture}>
      <PageHeader
        title={isScheduler ? 'Schedules' : 'Generator'}
        current={current}
        compact
        toggles="auto"
        status={status}
        onMenu={() => setMenuOpen(true)}
        actions={
          isConsoleVisible ? (
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggleConsole}
              aria-label="Hide the console"
              title="Hide the console"
              className="h-11 w-11 shrink-0 rounded-full hover:bg-stone-200/60"
            >
              <Terminal className="h-5 w-5" />
            </Button>
          ) : null
        }
      />
      <AppMenu
        open={menuOpen}
        onOpenChange={setMenuOpen}
        current={current}
        pageTitle={status ? 'This page' : undefined}
        dev={
          <div className="space-y-0.5">
            <button type="button" className={DEV_ROW} onClick={() => { setMenuOpen(false); onToggleConsole(); }}>
              <Terminal className="h-4 w-4 shrink-0 text-stone-500" aria-hidden />
              {isConsoleVisible ? 'Hide the console' : 'Show the console'}
            </button>
            {onOpenAboutDialog && (
              <button type="button" className={DEV_ROW} onClick={() => { setMenuOpen(false); onOpenAboutDialog(); }}>
                <Info className="h-4 w-4 shrink-0 text-stone-500" aria-hidden />
                About
              </button>
            )}
          </div>
        }
      >
        {status ? (
          <p className="px-1 text-[15px] leading-snug text-muted-foreground" data-testid="generator-link-sentence">
            {status.tone === 'ok' ? 'Live updates are connected: job progress and new pictures show as they happen.' : status.sentence}
          </p>
        ) : null}
      </AppMenu>
    </div>
  );
};

export default HeaderSection;
