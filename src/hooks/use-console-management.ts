
import { useState, useEffect, useCallback } from 'react';

export interface ConsoleLog {
  id: string;
  timestamp: Date;
  type: 'info' | 'warn' | 'error' | 'debug';
  message: string;
  data?: any;
}

export const useConsoleManagement = () => {
  const [consoleVisible, setConsoleVisible] = useState(false);
  const [consoleLogs, setConsoleLogs] = useState<ConsoleLog[]>([]);
  
  const addConsoleLog = useCallback((log: Omit<ConsoleLog, 'id' | 'timestamp'>) => {
    setConsoleLogs(prevLogs => [
      ...prevLogs,
      {
        ...log,
        id: `log-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        timestamp: new Date()
      }
    ]);
  }, []);
  
  const clearConsoleLogs = useCallback(() => {
    setConsoleLogs([]);
  }, []);
  
  // Capture console logs
  useEffect(() => {
    const originalConsoleLog = console.log;
    const originalConsoleWarn = console.warn;
    const originalConsoleError = console.error;
    
    console.log = (...args: any[]) => {
      originalConsoleLog(...args);
      addConsoleLog({
        type: 'info',
        message: args.map(arg => (typeof arg === 'object' ? JSON.stringify(arg) : String(arg))).join(' ')
      });
    };
    
    console.warn = (...args: any[]) => {
      originalConsoleWarn(...args);
      addConsoleLog({
        type: 'warn',
        message: args.map(arg => (typeof arg === 'object' ? JSON.stringify(arg) : String(arg))).join(' ')
      });
    };
    
    console.error = (...args: any[]) => {
      originalConsoleError(...args);
      addConsoleLog({
        type: 'error',
        message: args.map(arg => (typeof arg === 'object' ? JSON.stringify(arg) : String(arg))).join(' ')
      });
    };
    
    return () => {
      console.log = originalConsoleLog;
      console.warn = originalConsoleWarn;
      console.error = originalConsoleError;
    };
  }, [addConsoleLog]);
  
  return {
    consoleVisible,
    setConsoleVisible,
    consoleLogs,
    addConsoleLog,
    clearConsoleLogs
  };
};
