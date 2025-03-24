
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
    nextCheckTime, // This is now Date | null from useDisplayState
    loadNewImage,
    checkImageModified,
    handleManualCheck,
    getImagePositionStyle
  } = useDisplayState(previewParams);

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
    if (processedUrl) {
      if (!isTransitioning) {
        loadNewImage(processedUrl);
      }
      
      const intervalId = window.setInterval(() => {
        if (processedUrl && !isLoading && !isTransitioning) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);

      return () => {
        window.clearInterval(intervalId);
      };
    }
  }, [params.output, params.refreshInterval, params.debugMode, isLoading, isTransitioning, loadNewImage, checkImageModified]);

  useEffect(() => {
    if (!imageUrl) return;

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
  
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    processedCaption = processedCaption?.replace(regex, value) || '';
  });
  
  return processedCaption;
};
