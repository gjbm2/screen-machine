import { useState, useEffect, useRef } from 'react';
import apiService from '@/utils/api';

const useThrottle = (callback: Function, delay: number) => {
  const lastCall = useRef(0);
  const lastArgs = useRef<any[]>([]);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  
  return (...args: any[]) => {
    const now = Date.now();
    const timeSinceLastCall = now - lastCall.current;
    
    lastArgs.current = args;
    
    if (timeSinceLastCall >= delay) {
      lastCall.current = now;
      callback(...args);
    } else {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      
      timeoutRef.current = setTimeout(() => {
        lastCall.current = Date.now();
        callback(...lastArgs.current);
        timeoutRef.current = null;
      }, delay - timeSinceLastCall);
    }
  };
};

export const useConsole = () => {
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [isConsoleVisible, setIsConsoleVisible] = useState(false);
  const logCounter = useRef<Record<string, number>>({});

  const addConsoleLogImpl = (message: string | object) => {
    const timestamp = new Date().toLocaleTimeString();
    let formattedMessage: string;
    
    if (typeof message === 'object' && message !== null) {
      try {
        if ('type' in message && 'message' in message && typeof message.message === 'string') {
          formattedMessage = `[${timestamp}] [${message.type}] ${message.message}`;
        } else {
          formattedMessage = `[${timestamp}] ${JSON.stringify(message)}`;
        }
      } catch (e) {
        formattedMessage = `[${timestamp}] ${String(message)}`;
      }
    } else {
      formattedMessage = `[${timestamp}] ${message}`;
    }
    
    const logKey = typeof message === 'object' ? JSON.stringify(message) : message;
    const lastCount = logCounter.current[logKey] || 0;
    
    if (lastCount > 0) {
      setConsoleLogs(prev => {
        const updatedLogs = [...prev];
        const lastIndex = updatedLogs.length - 1;
        if (lastIndex >= 0 && updatedLogs[lastIndex].includes(formattedMessage.substring(0, 50))) {
          logCounter.current[logKey]++;
          updatedLogs[lastIndex] = `${formattedMessage} (repeated ${logCounter.current[logKey]} times)`;
        } else {
          logCounter.current[logKey] = 1;
          updatedLogs.push(formattedMessage);
        }
        return updatedLogs;
      });
    } else {
      logCounter.current[logKey] = 1;
      setConsoleLogs(prev => [...prev, formattedMessage]);
    }
    
    try {
      const apiMessage = typeof message === 'object' ? JSON.stringify(message) : message;
      apiService.sendLog(apiMessage).catch(error => {
        console.error('Failed to send log to API:', error);
      });
    } catch (error) {
      console.error('Failed to send log to API:', error);
      const errorMsg = `[${new Date().toLocaleTimeString()}] ERROR: Failed to send log to API: ${error}`;
      setConsoleLogs(prev => [...prev, errorMsg]);
    }
  };

  const addConsoleLog = useThrottle(addConsoleLogImpl, 200);

  const handleCloseConsole = () => {
    setIsConsoleVisible(false);
    return false;
  };

  const toggleConsole = () => {
    const newState = !isConsoleVisible;
    setIsConsoleVisible(newState);
    return newState;
  };

  const clearConsole = () => {
    setConsoleLogs([]);
    logCounter.current = {};
  };

  return {
    consoleLogs,
    isConsoleVisible,
    addConsoleLog,
    handleCloseConsole,
    toggleConsole,
    setIsConsoleVisible,
    clearConsole
  };
};

export default useConsole;
