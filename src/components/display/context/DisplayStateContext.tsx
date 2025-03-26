
import React, { createContext, useContext, useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { DisplayParams } from '../types';
import { fetchOutputFiles, getNextCheckTime, extractMetadata } from '../utils';

interface DisplayStateContextType {
  // Core image state
  imageUrl: string | null;
  setImageUrl: (url: string | null) => void;
  imageKey: number;
  setImageKey: (key: number) => void;
  
  // Metadata and timing
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: Date | null;
  
  // Output files and metadata
  outputFiles: string[];
  metadata: Record<string, string>;
  
  // Loading states
  isLoading: boolean;
  isChecking: boolean;
  isLoadingMetadata: boolean;
  
  // Status flags
  imageChanged: boolean;
  isTransitioning: boolean;
  
  // Transition effects
  oldImageUrl: string | null;
  oldImageStyle: React.CSSProperties;
  newImageStyle: React.CSSProperties;
  
  // Actions
  loadNewImage: (url: string) => void;
  handleManualCheck: () => Promise<boolean>;
  checkImageModified: (url: string) => Promise<boolean>;
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>;
  getImagePositionStyle: (position: DisplayParams['position'], showMode: DisplayParams['showMode'], containerWidth: number, containerHeight: number, imageWidth: number, imageHeight: number) => React.CSSProperties;
  handleImageError: () => void;
  
  // References
  imageRef: React.RefObject<HTMLImageElement>;
  
  // Processed data
  processedCaption: string | null;
  setProcessedCaption: (caption: string | null) => void;
  
  // Debug mode
  debugMode: boolean;
}

const DEFAULT_CONTEXT: DisplayStateContextType = {
  imageUrl: null,
  setImageUrl: () => {},
  imageKey: 0,
  setImageKey: () => {},
  lastModified: null,
  lastChecked: null,
  nextCheckTime: null,
  outputFiles: [],
  metadata: {},
  isLoading: false,
  isChecking: false,
  isLoadingMetadata: false,
  imageChanged: false,
  isTransitioning: false,
  oldImageUrl: null,
  oldImageStyle: {},
  newImageStyle: {},
  loadNewImage: () => {},
  handleManualCheck: async () => false,
  checkImageModified: async () => false,
  extractMetadataFromImage: async () => ({}),
  getImagePositionStyle: () => ({}),
  handleImageError: () => {},
  imageRef: { current: null },
  processedCaption: null,
  setProcessedCaption: () => {},
  debugMode: false
};

export const DisplayStateContext = createContext<DisplayStateContextType>(DEFAULT_CONTEXT);

export const useDisplayStateContext = () => useContext(DisplayStateContext);

interface DisplayStateProviderProps {
  children: React.ReactNode;
  params: DisplayParams;
}

export const DisplayStateProvider: React.FC<DisplayStateProviderProps> = ({ children, params }) => {
  // Core image state
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  
  // Metadata and timing
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  
  // Output files and metadata
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  
  // Loading states
  const [isLoading, setIsLoading] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [isLoadingMetadata, setIsLoadingMetadata] = useState(false);
  
  // Status flags
  const [imageChanged, setImageChanged] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  
  // Transition effects
  const [oldImageUrl, setOldImageUrl] = useState<string | null>(null);
  const [oldImageStyle, setOldImageStyle] = useState<React.CSSProperties>({});
  const [newImageStyle, setNewImageStyle] = useState<React.CSSProperties>({});
  
  // Processed data
  const [processedCaption, setProcessedCaption] = useState<string | null>(null);
  
  // References
  const imageRef = useRef<HTMLImageElement>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const isMountedRef = useRef(true);
  const lastMetadataUrlRef = useRef<string | null>(null);
  const isExtractingMetadataRef = useRef(false);
  const pollingEnabledRef = useRef(false);
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const debounceTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const fetchingOutputFilesRef = useRef(false);
  
  // User explicit debug exit tracking
  const userExplicitlyExitedDebugRef = useRef(false);
  
  // Cache for output files
  const outputFilesCache = useRef({
    timestamp: 0,
    files: [] as string[],
    expiryTime: 60000 // 60 seconds
  });
  
  // Set up mounted ref for cleanup
  useEffect(() => {
    isMountedRef.current = true;
    console.log('[DisplayStateProvider] Component mounted');
    
    return () => {
      console.log('[DisplayStateProvider] Component unmounting, cleaning up');
      isMountedRef.current = false;
      
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
      
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
    };
  }, []);
  
  // Memoize the nextCheckTime to prevent unnecessary recalculations
  const nextCheckTime = useMemo(() => {
    return getNextCheckTime(lastChecked, params.refreshInterval);
  }, [lastChecked, params.refreshInterval]);
  
  // Check if image has been modified
  const checkImageModified = useCallback(async (url: string): Promise<boolean> => {
    if (!isMountedRef.current || !url) return false;
    
    try {
      // Always update the last checked timestamp
      const currentTime = new Date();
      setLastChecked(currentTime);
      
      try {
        const response = await fetch(url, { 
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
          lastModifiedRef.current = new Date().toISOString();
          return true;
        }
      }
    } catch (err) {
      console.error('[checkImageModified] Error checking image modification:', err);
    }
    
    return false;
  }, []);
  
  // Extract metadata from an image
  const extractMetadataFromImage = useCallback(async (url: string): Promise<Record<string, string>> => {
    if (!isMountedRef.current || !url) return {};
    
    // If URL is the same and we already have metadata, return it
    if (url === lastMetadataUrlRef.current && Object.keys(metadata).length > 0) {
      return metadata;
    }
    
    // Prevent multiple concurrent extractions
    if (isExtractingMetadataRef.current) {
      return metadata;
    }
    
    setIsLoadingMetadata(true);
    isExtractingMetadataRef.current = true;
    
    try {
      const newMetadata = await extractMetadata(url);
      
      if (isMountedRef.current) {
        setMetadata(newMetadata);
        lastMetadataUrlRef.current = url;
      }
      
      return newMetadata;
    } catch (err) {
      console.error('[extractMetadataFromImage] Error extracting metadata:', err);
      return {};
    } finally {
      if (isMountedRef.current) {
        setIsLoadingMetadata(false);
        isExtractingMetadataRef.current = false;
      }
    }
  }, [metadata]);
  
  // Function to fetch output files with caching
  const fetchDebugOutputFiles = useCallback(async () => {
    if (!isMountedRef.current || !params.debugMode || fetchingOutputFilesRef.current) {
      return;
    }
    
    // Use cache if valid
    const now = Date.now();
    if (outputFilesCache.current.files.length > 0 && 
        now - outputFilesCache.current.timestamp < outputFilesCache.current.expiryTime) {
      return outputFilesCache.current.files;
    }
    
    // Prevent concurrent fetches
    fetchingOutputFilesRef.current = true;
    
    try {
      console.log('[fetchDebugOutputFiles] Fetching output files');
      const files = await fetchOutputFiles();
      
      if (isMountedRef.current) {
        // Only update if files actually changed
        const filesChanged = JSON.stringify(files) !== JSON.stringify(outputFiles);
        
        if (filesChanged) {
          setOutputFiles(files);
          console.log('[fetchDebugOutputFiles] Updated output files:', files);
        }
        
        // Update cache regardless
        outputFilesCache.current = {
          timestamp: now,
          files,
          expiryTime: 60000 // 60 seconds
        };
      }
      
      return files;
    } catch (err) {
      console.error('[fetchDebugOutputFiles] Error fetching files:', err);
    } finally {
      fetchingOutputFilesRef.current = false;
    }
  }, [outputFiles, params.debugMode]);
  
  // Set up centralized polling for debug files and image changes
  useEffect(() => {
    // Clear any existing interval
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    
    // Determine if polling should be enabled
    const shouldPollFiles = params.debugMode;
    const shouldPollImage = !!params.output && params.refreshInterval > 0;
    pollingEnabledRef.current = shouldPollFiles || shouldPollImage;
    
    if (!pollingEnabledRef.current) {
      return;
    }
    
    // Calculate polling interval - use the smaller of debug files polling or image refresh
    // But ensure a minimum of 5 seconds to prevent excessive API calls
    const debugInterval = 30; // 30 seconds for debug files
    const imageInterval = params.refreshInterval > 0 ? params.refreshInterval : 30;
    const effectiveInterval = Math.max(5, Math.min(debugInterval, imageInterval)) * 1000;
    
    console.log(`[DisplayStateProvider] Setting up polling with interval: ${effectiveInterval}ms`);
    
    // Run initial polls
    if (shouldPollFiles) {
      fetchDebugOutputFiles();
    }
    
    if (shouldPollImage && imageUrl) {
      checkImageModified(imageUrl).then(changed => {
        if (changed && isMountedRef.current) {
          loadNewImage(imageUrl);
        }
      });
    }
    
    // Set up interval for future polls
    pollingIntervalRef.current = setInterval(() => {
      if (!isMountedRef.current) {
        // Clean up if component unmounted
        if (pollingIntervalRef.current) {
          clearInterval(pollingIntervalRef.current);
          pollingIntervalRef.current = null;
        }
        return;
      }
      
      // Poll for debug files if in debug mode
      if (shouldPollFiles) {
        fetchDebugOutputFiles();
      }
      
      // Poll for image changes if enabled and we have an image
      if (shouldPollImage && imageUrl && !isLoading && !isTransitioning) {
        checkImageModified(imageUrl).then(changed => {
          if (changed && isMountedRef.current) {
            loadNewImage(imageUrl);
          }
        });
      }
    }, effectiveInterval);
    
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, [params.debugMode, params.output, params.refreshInterval, imageUrl, isLoading, isTransitioning, checkImageModified, fetchDebugOutputFiles]);
  
  // Load image with transition effect
  const loadNewImage = useCallback((url: string) => {
    if (!isMountedRef.current || !url) return;
    
    console.log('[loadNewImage] Loading new image:', url);
    setIsLoading(true);
    
    // Initialize transition
    setOldImageUrl(imageUrl);
    setIsTransitioning(true);
    
    // Set transition styles based on parameters
    const baseTransitionStyle = {
      position: 'absolute',
      top: 0,
      left: 0,
      width: '100%',
      height: '100%',
      opacity: 0,
      transition: 'opacity 0.5s ease-in-out'
    };
    
    setOldImageStyle({
      ...baseTransitionStyle,
      opacity: 1
    });
    
    setNewImageStyle({
      ...baseTransitionStyle,
      opacity: 0
    });
    
    // Update image URL and key to force reload
    setImageUrl(url);
    setImageKey(prevKey => prevKey + 1);
    
    // Automatically extract metadata when loading a new image
    extractMetadataFromImage(url);
    
    // Complete transition in next tick
    setTimeout(() => {
      if (!isMountedRef.current) return;
      
      setOldImageStyle({
        ...baseTransitionStyle,
        opacity: 0
      });
      
      setNewImageStyle({
        ...baseTransitionStyle,
        opacity: 1
      });
      
      // Clear transition after animation completes
      setTimeout(() => {
        if (!isMountedRef.current) return;
        
        setIsTransitioning(false);
        setOldImageUrl(null);
        setIsLoading(false);
      }, 500);
    }, 50);
  }, [imageUrl, extractMetadataFromImage]);
  
  // Handle manual check - combines all checking logic
  const handleManualCheck = useCallback(async (): Promise<boolean> => {
    if (!isMountedRef.current || isChecking) return false;
    
    setIsChecking(true);
    
    try {
      // Fetch debug files if in debug mode
      if (params.debugMode) {
        await fetchDebugOutputFiles();
      }
      
      // Check for image changes if we have an image URL
      let changed = false;
      if (imageUrl) {
        changed = await checkImageModified(imageUrl);
        
        if (changed) {
          loadNewImage(imageUrl);
          // Refresh metadata even if URL is the same
          extractMetadataFromImage(imageUrl);
        }
      }
      
      return changed;
    } catch (err) {
      console.error('[handleManualCheck] Error during manual check:', err);
      return false;
    } finally {
      if (isMountedRef.current) {
        setIsChecking(false);
      }
    }
  }, [imageUrl, isChecking, params.debugMode, checkImageModified, loadNewImage, extractMetadataFromImage, fetchDebugOutputFiles]);
  
  // Handle image loading error
  const handleImageError = useCallback(() => {
    if (!isMountedRef.current) return;
    
    console.error('[handleImageError] Failed to load image:', imageUrl);
    setError(`Failed to load image: ${imageUrl}`);
    setIsLoading(false);
    setIsTransitioning(false);
  }, [imageUrl]);
  
  // Calculate image positioning style
  const getImagePositionStyle = useCallback((
    position: DisplayParams['position'],
    showMode: DisplayParams['showMode'],
    containerWidth: number,
    containerHeight: number,
    imageWidth: number,
    imageHeight: number
  ): React.CSSProperties => {
    const style: React.CSSProperties = {
      position: 'absolute',
      maxWidth: '100%',
      maxHeight: '100%'
    };
    
    // Calculate position based on showMode
    if (showMode === 'contain') {
      style.maxWidth = '100%';
      style.maxHeight = '100%';
      style.width = 'auto';
      style.height = 'auto';
    } else if (showMode === 'cover') {
      style.width = '100%';
      style.height = '100%';
      style.objectFit = 'cover';
    } else if (showMode === 'actual') {
      style.maxWidth = 'none';
      style.maxHeight = 'none';
      style.width = 'auto';
      style.height = 'auto';
    }
    
    // Calculate position
    switch (position) {
      case 'top-left':
        style.top = 0;
        style.left = 0;
        break;
      case 'top-center':
        style.top = 0;
        style.left = '50%';
        style.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        style.top = 0;
        style.right = 0;
        break;
      case 'center-left':
        style.top = '50%';
        style.left = 0;
        style.transform = 'translateY(-50%)';
        break;
      case 'center':
        style.top = '50%';
        style.left = '50%';
        style.transform = 'translate(-50%, -50%)';
        break;
      case 'center-right':
        style.top = '50%';
        style.right = 0;
        style.transform = 'translateY(-50%)';
        break;
      case 'bottom-left':
        style.bottom = 0;
        style.left = 0;
        break;
      case 'bottom-center':
        style.bottom = 0;
        style.left = '50%';
        style.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        style.bottom = 0;
        style.right = 0;
        break;
      default:
        style.top = '50%';
        style.left = '50%';
        style.transform = 'translate(-50%, -50%)';
    }
    
    return style;
  }, []);
  
  // Process caption with metadata
  useEffect(() => {
    if (!isMountedRef.current) return;
    
    const caption = params.caption;
    
    // If no caption is set, clear it
    if (!caption) {
      setProcessedCaption(null);
      return;
    }
    
    // Process the caption template with metadata
    try {
      let processedTemplate = caption;
      
      // Replace metadata tags
      if (Object.keys(metadata).length > 0) {
        for (const [key, value] of Object.entries(metadata)) {
          const tag = `{${key}}`;
          processedTemplate = processedTemplate.replace(new RegExp(tag, 'g'), String(value));
        }
      }
      
      // Replace any remaining tags with empty string
      processedTemplate = processedTemplate.replace(/{[^}]+}/g, '');
      
      setProcessedCaption(processedTemplate);
    } catch (err) {
      console.error('[DisplayStateProvider] Error processing caption:', err);
      setProcessedCaption(caption);
    }
  }, [params.caption, metadata]);
  
  // Initial load of debug files if in debug mode
  useEffect(() => {
    if (params.debugMode && isMountedRef.current) {
      fetchDebugOutputFiles();
    }
  }, [params.debugMode, fetchDebugOutputFiles]);
  
  // Prepare the context value with memoization to prevent unnecessary re-renders
  const contextValue = useMemo<DisplayStateContextType>(() => ({
    // Core image state
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    
    // Metadata and timing
    lastModified,
    lastChecked,
    nextCheckTime,
    
    // Output files and metadata
    outputFiles,
    metadata,
    
    // Loading states
    isLoading,
    isChecking,
    isLoadingMetadata,
    
    // Status flags
    imageChanged,
    isTransitioning,
    
    // Transition effects
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    
    // Actions
    loadNewImage,
    handleManualCheck,
    checkImageModified,
    extractMetadataFromImage,
    getImagePositionStyle,
    handleImageError,
    
    // References
    imageRef,
    
    // Processed data
    processedCaption,
    setProcessedCaption,
    
    // Debug mode
    debugMode: params.debugMode
  }), [
    imageUrl, imageKey, lastModified, lastChecked, nextCheckTime, 
    outputFiles, metadata, isLoading, isChecking, isLoadingMetadata,
    imageChanged, isTransitioning, oldImageUrl, oldImageStyle, newImageStyle,
    loadNewImage, handleManualCheck, checkImageModified, extractMetadataFromImage,
    getImagePositionStyle, handleImageError, processedCaption, params.debugMode
  ]);
  
  return (
    <DisplayStateContext.Provider value={contextValue}>
      {children}
    </DisplayStateContext.Provider>
  );
};
