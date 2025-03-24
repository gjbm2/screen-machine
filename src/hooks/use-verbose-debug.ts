
import { useState, useEffect } from 'react';

export const useVerboseDebugMode = () => {
  const [isVerboseDebug, setVerboseDebug] = useState<boolean>(false);
  
  // Check URL parameters on mount
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const debug = urlParams.get('debug');
    
    if (debug === 'verbose') {
      console.info('🐛 Verbose debug mode enabled by URL parameter');
      setVerboseDebug(true);
    }
  }, []);
  
  // Update URL when verbose debug is toggled
  useEffect(() => {
    const url = new URL(window.location.href);
    
    if (isVerboseDebug) {
      url.searchParams.set('debug', 'verbose');
    } else {
      url.searchParams.delete('debug');
    }
    
    // Update the URL without refreshing the page
    window.history.replaceState({}, '', url.toString());
  }, [isVerboseDebug]);
  
  const toggleVerboseDebug = () => {
    setVerboseDebug(prev => !prev);
  };
  
  return {
    isVerboseDebug,
    setVerboseDebug,
    toggleVerboseDebug
  };
};
