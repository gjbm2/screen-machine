
import { useState, useRef } from 'react';
import { toast } from 'sonner';

export const useImageModificationCheck = () => {
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [imageChanged, setImageChanged] = useState<boolean>(false);
  const lastModifiedRef = useRef<string | null>(null);
  
  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      const checkUrl = url;
      
      try {
        const response = await fetch(checkUrl, { 
          method: 'HEAD', 
          cache: 'no-store',
          headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
          }
        });
        const lastModified = response.headers.get('last-modified');
        
        setLastModified(lastModified);
        
        if (lastModified && lastModified !== lastModifiedRef.current) {
          console.log('[checkImageModified] Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
          
          if (lastModifiedRef.current !== null) {
            setImageChanged(true);
            toast.info("Image has been updated on the server");
            lastModifiedRef.current = lastModified;
            return true;
          }
          
          lastModifiedRef.current = lastModified;
        }
        return false;
      } catch (e) {
        console.warn('[checkImageModified] HEAD request failed, falling back to image reload check:', e);
        
        if (lastModifiedRef.current === null) {
          setImageChanged(true);
          toast.info("Image may have been updated");
          lastModifiedRef.current = new Date().toISOString();
          return true;
        }
      }
    } catch (err) {
      console.error('[checkImageModified] Error checking image modification:', err);
      return false;
    }
    return false;
  };

  return {
    lastModified,
    setLastModified,
    lastChecked,
    setLastChecked,
    imageChanged,
    setImageChanged,
    lastModifiedRef,
    checkImageModified
  };
};
