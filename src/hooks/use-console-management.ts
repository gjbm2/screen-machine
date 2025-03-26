
import { useState, useCallback } from 'react';

export const useConsoleManagement = (addConsoleLog?: (log: any) => void) => {
  const [consoleVisible, setConsoleVisible] = useState(false);
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  
  // Reference to track console logs
  const consoleLogsRef = { current: [] as string[] };
  
  const addLog = useCallback((log: any) => {
    // Convert non-string logs to strings
    let stringLog: string;
    
    if (typeof log === 'string') {
      stringLog = log;
    } else if (typeof log === 'object' && log !== null) {
      try {
        if ('type' in log && 'message' in log && typeof log.message === 'string') {
          stringLog = `[${log.type}] ${log.message}`;
        } else {
          stringLog = JSON.stringify(log);
        }
      } catch (e) {
        stringLog = String(log);
      }
    } else {
      stringLog = String(log);
    }
    
    setConsoleLogs((prevLogs) => [...prevLogs, stringLog]);
    consoleLogsRef.current = [...consoleLogsRef.current, stringLog];
    
    if (addConsoleLog) {
      addConsoleLog(log);
    }
  }, [addConsoleLog]);

  const toggleConsole = useCallback(() => {
    setConsoleVisible(prev => !prev);
  }, []);

  const clearConsole = useCallback(() => {
    setConsoleLogs([]);
    consoleLogsRef.current = [];
  }, []);

  return {
    consoleVisible,
    consoleLogs,
    consoleLogsRef,
    addLog,
    toggleConsole,
    clearConsole
  };
};
