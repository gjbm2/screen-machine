
import { useState, useCallback } from 'react';

export const useConsoleManagement = (addConsoleLog?: (log: any) => void) => {
  const [consoleVisible, setConsoleVisible] = useState(false);
  const [consoleLogs, setConsoleLogs] = useState<any[]>([]);
  
  // Reference to track console logs
  const consoleLogsRef = { current: [] as any[] };
  
  const addLog = useCallback((log: any) => {
    setConsoleLogs((prevLogs) => [...prevLogs, log]);
    consoleLogsRef.current = [...consoleLogsRef.current, log];
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
