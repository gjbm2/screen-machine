
import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  processOutputParam, 
  fetchOutputFiles, 
  extractImageMetadata, 
  processCaptionWithMetadata,
  getNextCheckTime
} from '../utils';
import { DisplayParams } from '../types';
import { toast } from 'sonner';

export const useDisplayState = (params: DisplayParams) => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [imageChanged, setImageChanged] = useState<boolean>(false);
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [processedCaption, setProcessedCaption] = useState<string | null>(null);
  const [isTransitioning, setIsTransitioning] = useState<boolean>(false);
  const [oldImageUrl, setOldImageUrl] = useState<string | null>(null);
  const [oldImageStyle, setOldImageStyle] = useState<React.CSSProperties>({});
  const [newImageStyle, setNewImageStyle] = useState<React.CSSProperties>({});
  
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);
  const preloadImageRef = useRef<HTMLImageElement | null>(null);
  const navigate = useNavigate();

  const loadNewImage = async (url: string) => {
    if (params.transition === 'cut' || !imageUrl) {
      setImageUrl(url);
      setImageKey(prev => prev + 1);
      setImageChanged(false);
      
      // Extract metadata whenever a new image is loaded
      try {
        const newMetadata = await extractImageMetadata(url);
        setMetadata(newMetadata);
        
        // Process caption with new metadata if caption exists
        if (params.caption) {
          const newCaption = processCaptionWithMetadata(params.caption, newMetadata);
          setProcessedCaption(newCaption);
        }
      } catch (err) {
        console.error('Error extracting metadata:', err);
        toast.error("Failed to extract metadata");
      }
    } else {
      setIsLoading(true);
      setOldImageUrl(imageUrl);
      
      const preloadImg = new Image();
      preloadImg.onload = async () => {
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        
        // Extract metadata for the new image
        try {
          const newMetadata = await extractImageMetadata(url);
          setMetadata(newMetadata);
          
          // Process caption with new metadata if caption exists
          if (params.caption) {
            const newCaption = processCaptionWithMetadata(params.caption, newMetadata);
            setProcessedCaption(newCaption);
          }
        } catch (err) {
          console.error('Error extracting metadata:', err);
        }
        
        const duration = params.transition === 'fade-fast' ? 1 : 2;
        setOldImageStyle({
          position: 'absolute',
          transition: `opacity ${duration}s ease`,
          opacity: 1,
          zIndex: 2,
          ...getImagePositionStyle(params.position, params.showMode)
        });
        
        setNewImageStyle({
          ...getImagePositionStyle(params.position, params.showMode),
          opacity: 0,
          zIndex: 1
        });
        
        setIsTransitioning(true);
        
        setTimeout(() => {
          setOldImageStyle(prev => ({
            ...prev,
            opacity: 0
          }));
          
          setNewImageStyle(prev => ({
            ...prev,
            opacity: 1,
            transition: `opacity ${duration}s ease`
          }));
          
          setTimeout(() => {
            setIsTransitioning(false);
            setOldImageUrl(null);
            setImageChanged(false);
          }, duration * 1000);
        }, 50);
        
        setIsLoading(false);
      };
      
      preloadImg.onerror = () => {
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        setIsLoading(false);
        setImageChanged(false);
        toast.error("Failed to preload image for transition");
      };
      
      preloadImg.src = url;
      preloadImageRef.current = preloadImg;
    }
  };

  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      setLastModified(lastModified);
      
      if (lastModified && lastModified !== lastModifiedRef.current) {
        console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
        
        if (lastModifiedRef.current !== null) {
          setImageChanged(true);
          if (params.debugMode) {
            toast.info("Image has been updated on the server");
          }
          
          loadNewImage(url);
        }
        
        lastModifiedRef.current = lastModified;
      }
    } catch (err) {
      console.error('Error checking image modification:', err);
    }
  };

  const handleManualCheck = async () => {
    if (imageUrl) {
      setImageChanged(false);
      await checkImageModified(imageUrl);
      if (!imageChanged) {
        toast.info("Image has not changed since last check");
      }
    } else {
      toast.error("No image URL to check");
    }
  };

  const getImagePositionStyle = (position: string, showMode: string): React.CSSProperties => {
    const baseStyle: React.CSSProperties = (() => {
      switch (showMode) {
        case 'fill':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          };
        case 'fit':
          return {
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
          };
        case 'stretch':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'fill',
          };
        case 'actual':
          return {
            width: 'auto',
            height: 'auto',
            objectFit: 'none',
          };
        default:
          return {
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
          };
      }
    })();
    
    const positionStyle: React.CSSProperties = { position: 'absolute' };
    
    switch (position) {
      case 'top-left':
        return { ...baseStyle, ...positionStyle, top: 0, left: 0 };
      case 'top-center':
        return { ...baseStyle, ...positionStyle, top: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'top-right':
        return { ...baseStyle, ...positionStyle, top: 0, right: 0 };
      case 'center-left':
        return { ...baseStyle, ...positionStyle, top: '50%', left: 0, transform: 'translateY(-50%)' };
      case 'center':
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
      case 'center-right':
        return { ...baseStyle, ...positionStyle, top: '50%', right: 0, transform: 'translateY(-50%)' };
      case 'bottom-left':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: 0 };
      case 'bottom-center':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'bottom-right':
        return { ...baseStyle, ...positionStyle, bottom: 0, right: 0 };
      default:
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
    }
  };

  const nextCheckTime = getNextCheckTime(lastChecked, params.refreshInterval);

  return {
    imageUrl,
    setImageUrl,
    error,
    setError,
    imageKey,
    lastModified,
    lastChecked,
    outputFiles,
    setOutputFiles,
    imageChanged,
    metadata,
    isLoading,
    processedCaption,
    setProcessedCaption,
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    imageRef,
    nextCheckTime,
    loadNewImage,
    checkImageModified,
    handleManualCheck,
    getImagePositionStyle
  };
};
