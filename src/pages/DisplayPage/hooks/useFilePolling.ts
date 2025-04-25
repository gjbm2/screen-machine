
import { useState, useEffect, useRef } from 'react';

// Expanded interface to include all the properties we're returning
interface FilePollingResult {
  currentSrc: string;
  videoKey: string;
  fadeInSrc: string | null;
  fadeInVisible: boolean;
}

export default function useFilePolling(baseFileName: string | undefined): FilePollingResult {
  const [currentSrc, setCurrentSrc] = useState<string>('');
  const [videoKey, setVideoKey] = useState<string>(Date.now().toString());
  const [fadeInSrc, setFadeInSrc] = useState<string | null>(null);
  const [fadeInVisible, setFadeInVisible] = useState<boolean>(false);
  const lastPolled = useRef<number>(0);

  // Implement a polling mechanism to check for new files
  useEffect(() => {
    if (!baseFileName) return;
    
    const checkForUpdates = async () => {
      try {
        const now = Date.now();
        // Prevent excessive polling
        if (now - lastPolled.current < 1000) return;
        lastPolled.current = now;
        
        // Add a cache-busting query param
        const url = `/api/display/${baseFileName}?t=${now}`;
        
        const response = await fetch(url);
        if (!response.ok) return;
        
        const data = await response.json();
        
        if (data?.url && data.url !== currentSrc) {
          console.log(`[polling] File changed: ${data.url}`);
          
          // If this is a fade transition
          if (data.transition === 'fade' && currentSrc) {
            setFadeInSrc(data.url);
            setFadeInVisible(true);
            
            // After transition completes, set as main image
            setTimeout(() => {
              setCurrentSrc(data.url);
              setVideoKey(Date.now().toString());
              setFadeInSrc(null);
              setFadeInVisible(false);
            }, 1000); // Match fade-in duration in CSS
          } else {
            // Regular update without transition
            setCurrentSrc(data.url);
            setVideoKey(Date.now().toString());
          }
        }
      } catch (error) {
        console.error('[polling] Error checking for updates:', error);
      }
    };
    
    checkForUpdates();
    const interval = setInterval(checkForUpdates, 2000);
    
    return () => clearInterval(interval);
  }, [baseFileName, currentSrc]);
  
  return { currentSrc, videoKey, fadeInSrc, fadeInVisible };
}
