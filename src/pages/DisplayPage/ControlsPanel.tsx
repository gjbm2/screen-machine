// The hover/tap controls panel of the display page (file info, scheduler status, event triggers,
// the published picture's clips, fit/fill and mask toggles). Moved out of index.tsx unchanged so that its own error
// boundary can catch a crash in it (JSX evaluated in the parent's render would take the whole page down).
import { Play, Zap } from "lucide-react";
import type { PublishedClip } from "@/shared/api/buckets";

/** A clip of the published picture, for the Play buttons. */
export interface PanelClip extends PublishedClip {
  /** It is the one this page shows (Play plays it at once; another is put up first). */
  onScreen: boolean;
}

export interface SchedulerStatus {
  is_running?: boolean;
  is_paused?: boolean;
}

export interface NextAction {
  next_time?: string;
  description?: string;
}

interface Props {
  isMobile: boolean;
  fileName: string | null;
  fileType: string | null;
  lastModified: number;
  schedulerStatus: SchedulerStatus | null;
  nextEvent: NextAction | null;
  eventTriggers: string[];
  displayMode: "fit" | "fill";
  noMask: boolean;
  toggleDisplayMode: () => void;
  toggleNoMask: () => void;
  handleThrowEvent: (eventKey: string) => void;
  clips: PanelClip[];
  /** The clip on screen is resting on its picture, so Play can start it. */
  canPlayNow: boolean;
  /** The clip being put up to play, if any. */
  startingClip: string | null;
  onPlayClip: (clip: PanelClip) => void;
}

function clipLabel(c: PanelClip, several: boolean): string {
  if (!several) return "Play";
  const pace = c.pace ? c.pace[0].toUpperCase() + c.pace.slice(1) : "Clip";
  return `${pace}${c.play_s ? ` (${Math.round(c.play_s)} s)` : ""}${c.onScreen ? " · on screen" : ""}`;
}

export function ControlsPanel({
  isMobile,
  fileName,
  fileType,
  lastModified,
  schedulerStatus,
  nextEvent,
  eventTriggers,
  displayMode,
  noMask,
  toggleDisplayMode,
  toggleNoMask,
  handleThrowEvent,
  clips,
  canPlayNow,
  startingClip,
  onPlayClip,
}: Props) {
  return (
    <div
      style={{
        position: 'fixed',
        top: 20,
        right: 20,
        zIndex: 100000,
        background: 'rgba(0,0,0,0.7)',
        color: 'white',
        borderRadius: 12,
        padding: '1em 1.5em',
        boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
        display: 'flex',
        flexDirection: 'column',
        gap: '1em',
        fontSize: isMobile ? 16 : 18,
        cursor: 'pointer',
        userSelect: 'none',
        minWidth: isMobile ? 200 : 260,
        maxWidth: '90vw',
      }}
    >
      {/* File info */}
      <div style={{fontSize: 15, opacity: 0.85, marginBottom: 8}}>
        <div><b>File:</b> {fileName || '—'}</div>
        <div><b>Type:</b> {fileType || '—'}</div>
        <div><b>Last update:</b> {lastModified ? new Date(lastModified).toLocaleString() : '—'}</div>
      </div>
      {/* Scheduler status */}
      <div style={{fontSize: 15, opacity: 0.85, marginBottom: 8}}>
        <div><b>Scheduler:</b> {schedulerStatus ? (schedulerStatus.is_running ? 'Running' : (schedulerStatus.is_paused ? 'Paused' : 'Stopped')) : '—'}</div>
        <div><b>Next event:</b> {nextEvent && nextEvent.next_time ? `${nextEvent.description || ''} (${nextEvent.next_time})` : '—'}</div>
      </div>
      {/* The published picture's clips: Play starts the one on screen now (no waiting out its rest); another is put
          up and plays as soon as it lands */}
      {clips.length > 0 && (
        <div style={{marginBottom: 8}}>
          <div style={{fontWeight: 600, fontSize: 15, marginBottom: 4}}>{clips.length > 1 ? 'Clips:' : 'Clip:'}</div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 8}}>
            {clips.map(c => {
              const playing = c.onScreen && !canPlayNow && startingClip !== c.filename;
              const starting = startingClip === c.filename;
              return (
                <button
                  key={c.filename}
                  disabled={playing || starting}
                  onClick={e => { e.stopPropagation(); onPlayClip(c); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    background: 'rgba(129, 199, 132, 0.14)',
                    border: '1px solid #81c78466',
                    color: '#a5d6a7',
                    borderRadius: 6,
                    padding: isMobile ? '6px 14px' : '2px 10px',
                    fontSize: 15,
                    cursor: playing || starting ? 'default' : 'pointer',
                    opacity: playing || starting ? 0.6 : 1,
                    fontWeight: 500,
                  }}
                  title={c.onScreen ? 'Play it now' : 'Put this clip up and play it'}
                >
                  <Play size={16} style={{marginRight: 4}} />
                  {starting ? 'Starting…' : playing ? 'Playing' : clipLabel(c, clips.length > 1)}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {/* Event triggers */}
      {eventTriggers.length > 0 && (
        <div style={{marginBottom: 8}}>
          <div style={{fontWeight: 600, fontSize: 15, marginBottom: 4}}>Event Triggers:</div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 8}}>
            {eventTriggers.map(trigger => (
              <button
                key={trigger}
                onClick={e => { e.stopPropagation(); handleThrowEvent(trigger); }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  background: 'rgba(255, 193, 7, 0.12)',
                  border: '1px solid #ffc10755',
                  color: '#ffc107',
                  borderRadius: 6,
                  padding: '2px 10px',
                  fontSize: 15,
                  cursor: 'pointer',
                  fontWeight: 500,
                }}
                title={`Trigger event: ${trigger}`}
              >
                <Zap size={16} style={{marginRight: 4}} /> {trigger}
              </button>
            ))}
          </div>
        </div>
      )}
      {/* Toggles */}
      <div onClick={e => { e.stopPropagation(); toggleDisplayMode(); }}
        style={{padding: '0.2em 0', cursor: 'pointer', textDecoration: 'underline', color: '#90caf9', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6}}>
        <span><b>Image:</b> {displayMode === 'fit' ? 'Fit' : 'Fill'}</span>
        <span style={{fontSize: 16, opacity: 0.7}}>&#8635;</span>
      </div>
      <div onClick={e => { e.stopPropagation(); toggleNoMask(); }}
        style={{padding: '0.2em 0', cursor: 'pointer', textDecoration: 'underline', color: '#90caf9', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6}}>
        <span><b>Mask:</b> {noMask ? 'Off' : 'On'}</span>
        <span style={{fontSize: 16, opacity: 0.7}}>&#8635;</span>
      </div>
    </div>
  );
}
