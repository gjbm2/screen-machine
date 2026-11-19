import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Routes, Route } from "react-router-dom";
import { lazy, Suspense, type ComponentType } from "react";
import { ReferenceImagesProvider } from "./contexts/ReferenceImagesContext";
import { LoopeViewProvider } from "@/contexts/LoopeViewContext";
import { DarkMode } from './darkMode';
import { AppPlayerProvider } from "@/components/player/appPlayer";
import { DockedPlayer } from "@/components/player/DockedPlayer";
import { AppNav } from "@/components/nav/AppNav";
import { AdminOnly } from "@/components/auth/AdminOnly";

// Each page is its own chunk: the TVs' /display/<screen> no longer loads the generator, Room, Music and the rest.
// A deploy replaces dist/, so a tab opened before it asks for chunks that are gone: reload once to pick up the new build.
function page<P extends object>(load: () => Promise<{ default: ComponentType<P> }>) {
  return lazy(() =>
    load().catch((e) => {
      if (reloadedJustNow()) throw e;
      window.location.reload();
      return new Promise<{ default: ComponentType<P> }>(() => {});
    }),
  );
}

function reloadedJustNow(): boolean {
  try {
    const t = Number(sessionStorage.getItem("sm_chunk_reload") ?? 0);
    if (Date.now() - t < 30_000) return true;
    sessionStorage.setItem("sm_chunk_reload", String(Date.now()));
    return false;
  } catch {
    return true; // no storage: show the error rather than risk a reload loop
  }
}

const DisplayPage = page(() => import("./pages/DisplayPage"));
const Index = page(() => import("./pages/Index"));
const Display = page(() => import("./pages/Display"));
const NotFound = page(() => import("./pages/NotFound"));
const SchemaEdit = page(() => import("./pages/SchemaEdit"));
const SchemaDynamicForm = page(() => import("./pages/SchemaDynamicForm"));
const Scheduler = page(() => import("./pages/scheduler.tsx"));
const JobsPage = page(() => import("./pages/Jobs"));
const LightSense = page(() => import("./pages/LightSense"));
const AndroidApp = page(() => import("./pages/AndroidApp"));
const AdminPanel = page(() => import("./pages/AdminPanel"));
const RoomPage = page(() => import("./pages/Room"));
const LightsPage = page(() => import("./pages/Lights"));
const LightsControlPage = page(() => import("./pages/Lights/Control"));
const MusicPage = page(() => import("./pages/Music"));
const SettingsPage = page(() => import("./pages/Settings"));
const StartPage = page(() => import("./pages/Settings/Start"));

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <ReferenceImagesProvider>
        <LoopeViewProvider>
          <DarkMode />
          <Toaster />
          <Sonner />
          <AppPlayerProvider>
          {/* the app's places: a top bar on iPad and desktop, a bottom tab bar on a phone */}
          <AppNav />
          <Suspense fallback={null}>
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/display/:screenId" element={<DisplayPage />} />
            <Route path="/display" element={<Display />} />
            <Route path="/schema-edit" element={<AdminOnly><SchemaEdit /></AdminOnly>} />
            <Route path="/schema-dynamic-form" element={<AdminOnly><SchemaDynamicForm /></AdminOnly>} />
            <Route path="/scheduler" element={<AdminOnly><Scheduler /></AdminOnly>} />
            <Route path="/jobs" element={<AdminOnly><JobsPage /></AdminOnly>} />
            <Route path="/lightsense" element={<AdminOnly><LightSense /></AdminOnly>} />
            <Route path="/androidapp" element={<AndroidApp />} />
            <Route path="/app" element={<AndroidApp />} />
            <Route path="/adminadmin" element={<AdminOnly><AdminPanel /></AdminOnly>} />
            <Route path="/room" element={<RoomPage />} />
            <Route path="/lights" element={<LightsControlPage />} />
            <Route path="/settings/lights" element={<AdminOnly><LightsPage /></AdminOnly>} />
            <Route path="/music" element={<MusicPage />} />
            <Route path="/settings" element={<AdminOnly><SettingsPage /></AdminOnly>} />
            {/* where the app opens: this device's choice in Settings (Room or Music) */}
            <Route path="/start" element={<StartPage />} />
            {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            <Route path="*" element={<NotFound />} />
          </Routes>
          </Suspense>
          {/* the music player, docked at the foot of the pages people use while music plays */}
          <DockedPlayer />
          </AppPlayerProvider>
        </LoopeViewProvider>
      </ReferenceImagesProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
