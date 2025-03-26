
import { useState, useEffect } from 'react';
import apiService from '@/utils/api';

export const useConsole = () => {
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [isConsoleVisible, setIsConsoleVisible] = useState(false);

  const addConsoleLog = async (message: string | object) => {
    const timestamp = new Date().toLocaleTimeString();
    let formattedMessage: string;
    
    // Handle different types of messages
    if (typeof message === 'object' && message !== null) {
      try {
        // Special handling for log objects with type and message
        if ('type' in message && 'message' in message && typeof message.message === 'string') {
          formattedMessage = `[${timestamp}] [${message.type}] ${message.message}`;
        } else {
          // For other objects, stringify them
          formattedMessage = `[${timestamp}] ${JSON.stringify(message)}`;
        }
      } catch (e) {
        formattedMessage = `[${timestamp}] ${String(message)}`;
      }
    } else {
      formattedMessage = `[${timestamp}] ${message}`;
    }
    
    setConsoleLogs(prev => [...prev, formattedMessage]);
    
    try {
      // Convert object messages to strings for API
      const apiMessage = typeof message === 'object' ? JSON.stringify(message) : message;
      await apiService.sendLog(apiMessage);
    } catch (error) {
      console.error('Failed to send log to API:', error);
      // Add error to console logs
      const errorMsg = `[${new Date().toLocaleTimeString()}] ERROR: Failed to send log to API: ${error}`;
      setConsoleLogs(prev => [...prev, errorMsg]);
    }
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
