import React from 'react';
import { SchedulerEventsPanel } from '../components/scheduler/SchedulerEventsPanel';
import MainLayout from '@/components/layout/MainLayout';

const SchedulerEventsTest: React.FC = () => {
  // Mock console management for testing
  const mockConsoleLogs: string[] = [];
  
  return (
    <MainLayout 
      onToggleConsole={() => {}}
      consoleVisible={false}
      onOpenAdvancedOptions={() => {}}
      consoleLogs={mockConsoleLogs}
      onClearConsole={() => {}}
    >
      <div className="container mx-auto p-4">
        <h1 className="text-3xl font-bold mb-6">Scheduler Events Test</h1>
        <SchedulerEventsPanel />
      </div>
    </MainLayout>
  );
};

export default SchedulerEventsTest; 