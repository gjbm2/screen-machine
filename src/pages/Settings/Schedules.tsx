// Settings › Schedules (#schedules): each screen's schedule, running, paused or stopped, with when it next does
// something (GET /api/schedulers/all/status, read once when opened), and a link to edit it on Schedules. Today's
// values (the theme and the like) are changed on the Room page's Today's art.

import { useEffect, useState } from "react";
import { CalendarClock } from "lucide-react";
import { schedulerUrl } from "@/components/nav/appNav";
import { getAllSchedulerStatuses } from "@/shared/api/scheduler";
import { Group, Hint, LinkRow } from "./Group";
import type { Destination } from "./Screens";

interface SchedStatus {
  status?: string;
  is_paused?: boolean;
  next_action?: { next_time?: string; time_until_display?: string } | null;
}

function words(s: SchedStatus | undefined): string {
  if (!s) return "No schedule";
  if (s.is_paused || s.status === "paused") return "Paused";
  if (s.status === "running") return s.next_action?.next_time ? `Running · next at ${s.next_action.next_time}` : "Running";
  return "Not running";
}

export function SchedulesGroup({ dests }: { dests: Destination[] | null }) {
  return (
    <Group id="schedules" title="Schedules" icon={<CalendarClock />} summary="What each screen does through the day">
      <SchedulesBody dests={dests} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function SchedulesBody({ dests }: { dests: Destination[] | null }) {
  const [all, setAll] = useState<Record<string, SchedStatus> | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    getAllSchedulerStatuses()
      .then((r) => alive && setAll((r?.statuses ?? {}) as Record<string, SchedStatus>))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const screens = (dests ?? []).filter((d) => !d.hidden && !d.id.startsWith("_"));

  return (
    <>
      {failed && <Hint>The app isn't answering, so the schedules can't be read.</Hint>}
      <ul className="divide-y divide-stone-200/80 rounded-2xl border border-stone-200/80 bg-card" data-testid="settings-schedules">
        {screens.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-x-3 px-3 py-1.5">
            <div className="min-w-0 flex-1 basis-40">
              <p className="text-base font-medium leading-snug">{d.name || d.id}</p>
              <p className="text-sm text-muted-foreground">{all ? words(all[d.id]) : "Reading…"}</p>
            </div>
            <LinkRow href={schedulerUrl(d.id)}>Schedule</LinkRow>
          </li>
        ))}
        {dests !== null && screens.length === 0 && <li className="px-3 py-2 text-[15px] text-muted-foreground">No screens.</li>}
      </ul>
      <Hint>Today's values, such as the day's theme, are changed on the Room page under Today's art.</Hint>
      <div className="flex flex-wrap gap-x-4">
        <LinkRow href={schedulerUrl()}>All schedules</LinkRow>
        <LinkRow href="/room#today">Today's art</LinkRow>
      </div>
    </>
  );
}
