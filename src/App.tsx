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
import { ReferenceImagesProvider } from "./contexts/ReferenceImagesContext";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <ReferenceImagesProvider>
        <Toaster />
        <Sonner />
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/display/:screenId" element={<DisplayPage />} />
          <Route path="/display" element={<Display />} />
          <Route path="/schema-edit" element={<SchemaEdit />} />
          <Route path="/schema-dynamic-form" element={<SchemaDynamicForm />} />
          <Route path="/scheduler" element={<Scheduler />} />
          {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ReferenceImagesProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
