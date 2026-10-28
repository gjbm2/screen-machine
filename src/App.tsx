import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Routes, Route } from "react-router-dom";
import DisplayPage from "./pages/DisplayPage";
import Index from "./pages/Index";
import Display from "./pages/Display";
import NotFound from "./pages/NotFound";
import SchemaEdit from './pages/SchemaEdit';
import SchemaDynamicForm from './pages/SchemaDynamicForm';
import Scheduler from './pages/scheduler.tsx';
import JobsPage from './pages/Jobs';
import { ReferenceImagesProvider } from "./contexts/ReferenceImagesContext";
import { LoopeViewProvider } from "@/contexts/LoopeViewContext";
import LightSense from './pages/LightSense';
import AndroidApp from './pages/AndroidApp';
import AdminPanel from './pages/AdminPanel';
import RoomPage from './pages/Room';
import LightsPage from './pages/Lights';
import LightsControlPage from './pages/Lights/Control';
import MusicPage from './pages/Music';
import SettingsPage from './pages/Settings';
import StartPage from './pages/Settings/Start';
import { DarkMode } from './darkMode';
import { AppPlayerProvider } from "@/components/player/appPlayer";
import { DockedPlayer } from "@/components/player/DockedPlayer";
import { AppNav } from "@/components/nav/AppNav";

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
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/display/:screenId" element={<DisplayPage />} />
            <Route path="/display" element={<Display />} />
            <Route path="/schema-edit" element={<SchemaEdit />} />
            <Route path="/schema-dynamic-form" element={<SchemaDynamicForm />} />
            <Route path="/scheduler" element={<Scheduler />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/lightsense" element={<LightSense />} />
            <Route path="/androidapp" element={<AndroidApp />} />
            <Route path="/app" element={<AndroidApp />} />
            <Route path="/adminadmin" element={<AdminPanel />} />
            <Route path="/room" element={<RoomPage />} />
            <Route path="/lights" element={<LightsControlPage />} />
            <Route path="/settings/lights" element={<LightsPage />} />
            <Route path="/music" element={<MusicPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            {/* where the app opens: this device's choice in Settings (Room or Music) */}
            <Route path="/start" element={<StartPage />} />
            {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            <Route path="*" element={<NotFound />} />
          </Routes>
          {/* the music player, docked at the foot of the pages people use while music plays */}
          <DockedPlayer />
          </AppPlayerProvider>
        </LoopeViewProvider>
      </ReferenceImagesProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
