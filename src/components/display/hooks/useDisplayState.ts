
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { getNextCheckTime } from '../utils';
import { DisplayParams } from '../types';
import { useImageState } from './useImageState';
import { useTransitionEffect } from './useTransitionEffect';
import { useCaptionState } from './useCaptionState';
import { useOutputFilesState } from './useOutputFilesState';
import { useImageStyler } from './useImageStyler';
import { useImageLoader } from './useImageLoader';

export const useDisplayState = (params: DisplayParams) => {
  // Combine all the smaller hooks
  const {
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    lastModified,
    setLastModified,
    lastChecked,
    setLastChecked,
    imageChanged,
    setImageChanged,
    metadata,
    setMetadata,
    isLoading,
    setIsLoading,
    imageRef,
    lastModifiedRef,
    checkImageModified,
    handleManualCheck,
    extractMetadataFromImage
  } = useImageState();

  const {
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    initializeTransition,
    executeTransition
  } = useTransitionEffect();

  const {
    processedCaption,
    setProcessedCaption,
    updateCaption
  } = useCaptionState();

  const {
    outputFiles,
    setOutputFiles,
    error,
    setError
  } = useOutputFilesState();

  const {
    getImagePositionStyle
  } = useImageStyler();

  const {
    loadNewImage: imageLoader
  } = useImageLoader(
    setIsLoading,
    setImageUrl,
    setImageKey,
    setImageChanged,
    extractMetadataFromImage,
    updateCaption,
    initializeTransition,
    executeTransition,
    getImagePositionStyle
  );

  // Adapter function to maintain the same API
  const loadNewImage = (url: string) => {
    return imageLoader(url, imageUrl, params);
  };

  // Maintains the original handleManualCheck API 
  // Fixed: Now we call handleManualCheck with no arguments
  const handleManualCheckOriginal = async () => {
    return handleManualCheck();
  };

  // Calculate next check time
  const nextCheckTime = getNextCheckTime(lastChecked, params.refreshInterval);

  return {
    imageUrl,
    setImageUrl,
    error,
    setError,
    imageKey,
    setImageKey,
    lastModified,
    lastChecked,
    outputFiles,
    setOutputFiles,
    imageChanged,
    setImageChanged,
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
    handleManualCheck: handleManualCheckOriginal,
    getImagePositionStyle,
    extractMetadataFromImage
  };
};
