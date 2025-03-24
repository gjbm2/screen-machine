
import { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { processOutputParam, fetchOutputFiles, extractImageMetadata, getNextCheckTime } from '@/components/display/utils';
import { DisplayParams } from '@/components/display/types';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';
import { toast } from 'sonner';

export const useDisplayPage = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const params: DisplayParams = {
    output: searchParams.get('output') ? decodeURIComponent(searchParams.get('output') || '') : null,
    showMode: (searchParams.get('show') || 'fit') as DisplayParams['showMode'],
    position: (searchParams.get('position') || 'center') as DisplayParams['position'],
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
    data: searchParams.has('data') ? searchParams.get('data') : undefined,
    caption: searchParams.get('caption') ? decodeURIComponent(searchParams.get('caption') || '') : null,
    captionPosition: searchParams.get('caption-position') as DisplayParams['captionPosition'] || 'bottom-center',
    captionSize: searchParams.get('caption-size') || '16px',
    captionColor: searchParams.get('caption-color') || 'ffffff',
    captionFont: searchParams.get('caption-font') ? decodeURIComponent(searchParams.get('caption-font') || '') : 'Arial, sans-serif',
    captionBgColor: searchParams.get('caption-bg-color') || '#000000',
    captionBgOpacity: searchParams.get('caption-bg-opacity') ? parseFloat(searchParams.get('caption-bg-opacity') || '0.7') : 0.7,
    transition: searchParams.get('transition') as DisplayParams['transition'] || 'cut',
  };

  const [previewParams, setPreviewParams] = useState<DisplayParams>(params);
  
  const {
    imageUrl,
    error,
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
    nextCheckTime, // This is now Date | null
    loadNewImage,
    checkImageModified,
    handleManualCheck: originalHandleManualCheck,
    getImagePositionStyle
  } = useDisplayState(previewParams);

  // Debug logging
  useEffect(() => {
    console.log('[useDisplayPage] Params:', params);
    console.log('[useDisplayPage] Image URL:', imageUrl);
    console.log('[useDisplayPage] Metadata:', metadata);
  }, [params, imageUrl, metadata]);

  useEffect(() => {
    if (!params.output && !params.debugMode) {
      const queryParams = new URLSearchParams();
      queryParams.set('debug', 'true');
      if (params.showMode) queryParams.set('show', params.showMode);
      if (params.position) queryParams.set('position', params.position);
      if (params.refreshInterval) queryParams.set('refresh', params.refreshInterval.toString());
      if (params.backgroundColor) queryParams.set('background', params.backgroundColor);
      navigate(`/display?${queryParams.toString()}`);
    }
  }, [params.output, params.debugMode, navigate, params.showMode, params.position, params.refreshInterval, params.backgroundColor]);

  useEffect(() => {
    if (!params.output && !params.debugMode) {
      return;
    }

    const processedUrl = processOutputParam(params.output);
    
    // More detailed logging
    console.log('[useDisplayPage] Processed URL:', processedUrl);
    console.log('[useDisplayPage] Is transitioning:', isTransitioning);
    console.log('[useDisplayPage] Is loading:', isLoading);
    
    if (processedUrl) {
      if (!isTransitioning) {
        loadNewImage(processedUrl);
      }
      
      const intervalId = window.setInterval(() => {
        if (processedUrl && !isLoading && !isTransitioning) {
          checkImageModified(processedUrl).then(changed => {
            if (changed) {
              console.log('[useDisplayPage] Image changed, should re-extract metadata');
            }
          });
        }
      }, params.refreshInterval * 1000);

      return () => {
        window.clearInterval(intervalId);
      };
    }
  }, [params.output, params.refreshInterval, params.debugMode, isLoading, isTransitioning, loadNewImage, checkImageModified]);

  // Enhanced metadata handling
  useEffect(() => {
    if (!imageUrl) return;

    // Force metadata extraction when image URL changes
    if (imageUrl) {
      console.log('[useDisplayPage] Image URL changed, should extract metadata');
    }

    if (previewParams.caption) {
      if (previewParams.data !== undefined) {
        if (Object.keys(metadata).length > 0) {
          const newCaption = processCaptionWithMetadata(previewParams.caption, metadata);
          setProcessedCaption(newCaption);
        }
      } else {
        setProcessedCaption(previewParams.caption);
      }
    } else {
      setProcessedCaption(null);
    }
  }, [previewParams.caption, previewParams.data, metadata, imageUrl, setProcessedCaption]);

  useEffect(() => {
    if (params.debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }
  }, [params.debugMode, setOutputFiles]);

  useEffect(() => {
    setPreviewParams(params);
  }, [params]);

  // Enhanced manual check that ensures metadata is refreshed
  const handleManualCheck = async () => {
    console.log('[useDisplayPage] Manual check initiated');
    
    if (imageUrl) {
      const result = await originalHandleManualCheck(imageUrl);
      
      // Force metadata refresh regardless of whether the image changed
      if (params.debugMode) {
        try {
          console.log('[useDisplayPage] Forcing metadata refresh on manual check');
          const extractedMetadata = await extractImageMetadata(imageUrl);
          console.log('[useDisplayPage] Manually extracted metadata:', extractedMetadata);
          
          if (Object.keys(extractedMetadata).length === 0) {
            toast.warning("No metadata found in this image");
          }
        } catch (err) {
          console.error('[useDisplayPage] Error during manual metadata extraction:', err);
        }
      }
      
      return result;
    }
    
    return false;
  };

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
    toast.error("Failed to load image");
  };

  return {
    params,
    previewParams,
    imageUrl,
    error,
    imageKey,
    lastModified,
    lastChecked,
    outputFiles,
    imageChanged,
    metadata,
    isLoading,
    processedCaption,
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    imageRef,
    nextCheckTime, // This is now Date | null
    handleManualCheck,
    getImagePositionStyle,
    handleImageError
  };
};

const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>) => {
  if (!caption) return null;
  
  let processedCaption = caption;
  
  // More detailed logging
  console.log('[processCaptionWithMetadata] Processing caption:', caption);
  console.log('[processCaptionWithMetadata] With metadata:', metadata);
  
  // Special case for {all} placeholder
  if (caption === '{all}') {
    const allMetadata = Object.entries(metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n');
    return allMetadata || 'No metadata available';
  }
  
  // Replace individual tags
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    processedCaption = processedCaption?.replace(regex, value) || '';
  });
  
  return processedCaption;
};
