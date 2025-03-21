
import { useState, useEffect } from 'react';
import apiService from '@/utils/api';

export const useConsole = () => {
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [isConsoleVisible, setIsConsoleVisible] = useState(false);

  const addConsoleLog = async (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    const formattedMessage = `[${timestamp}] ${message}`;
    
    setConsoleLogs(prev => [...prev, formattedMessage]);
    
    await apiService.sendLog(message);
  };

  const handleCloseConsole = () => {
    setIsConsoleVisible(false);
    return false; // Return the new state for consumers
  };

  const toggleConsole = () => {
    const newState = !isConsoleVisible;
    setIsConsoleVisible(newState);
    return newState; // Return the new state for consumers
  };

  return {
    consoleLogs,
    isConsoleVisible,
    addConsoleLog,
    handleCloseConsole,
    toggleConsole,
    setIsConsoleVisible
  };
};

export default useConsole;
