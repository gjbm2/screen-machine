
import { useState, useEffect, useCallback } from 'react';

export const useVerboseDebug = () => {
  const [verboseDebug, setVerboseDebug] = useState<boolean>(false);

  // Initialize from URL param on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const debugParam = params.get('debug');
    
    if (debugParam === 'verbose') {
      console.info('Verbose debugging enabled via URL parameter');
      setVerboseDebug(true);
    }
    
    // Also check localStorage for persistent setting
    const storedDebug = localStorage.getItem('verboseDebug');
    if (storedDebug === 'true') {
      console.info('Verbose debugging enabled via localStorage setting');
      setVerboseDebug(true);
    }
  }, []);

  // Toggle debug mode
  const toggleVerboseDebug = useCallback(() => {
    setVerboseDebug(prev => {
      const newValue = !prev;
      
      // Store in localStorage for persistence
      localStorage.setItem('verboseDebug', String(newValue));
      
      // Add or remove URL parameter
      const url = new URL(window.location.href);
      if (newValue) {
        url.searchParams.set('debug', 'verbose');
        console.info('Verbose debugging enabled');
      } else {
        url.searchParams.delete('debug');
        console.info('Verbose debugging disabled');
      }
      
      // Update URL without reloading page
      window.history.replaceState({}, '', url.toString());
      
      return newValue;
    });
  }, []);

  // Helper function to log verbose info
  const logVerbose = useCallback((message: string, data?: any) => {
    if (verboseDebug) {
      if (data) {
        console.info(`[VERBOSE] ${message}`, data);
      } else {
        console.info(`[VERBOSE] ${message}`);
      }
    }
  }, [verboseDebug]);

  return { verboseDebug, toggleVerboseDebug, logVerbose };
};
