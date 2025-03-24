
import { useState, useEffect } from 'react';
import { DisplayParams } from '../types';
import { useDebugPanelState } from './useDebugPanelState';
import { useDebugPanelFiles } from './useDebugPanelFiles';
import { useDebugPanelPosition } from './useDebugPanelPosition';
import { useDebugPanelCaption } from './useDebugPanelCaption';
import { useDebugPanelMetadata } from './useDebugPanelMetadata';

interface DebugPanelHookProps {
  params: DisplayParams;
  imageUrl: string | null;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
}

export const useDebugPanel = ({ params, imageUrl, metadata, onApplyCaption }: DebugPanelHookProps) => {
  // Use the individual hooks
  const {
    activeTab,
    setActiveTab,
    customUrl,
    setCustomUrl,
    showMode,
    setShowMode,
    position,
    setPosition,
    refreshInterval, 
    setRefreshInterval,
    backgroundColor,
    setBackgroundColor,
    caption,
    setCaption,
    captionPosition,
    setCaptionPosition,
    captionSize,
    setCaptionSize,
    captionColor,
    setCaptionColor,
    captionFont,
    setCaptionFont,
    captionBgColor,
    setCaptionBgColor,
    captionBgOpacity,
    setCaptionBgOpacity,
    transition,
    setTransition,
    copied,
    setCopied,
    metadataEntries,
    setMetadataEntries,
    previewCaption,
    setPreviewCaption,
    resetSettings
  } = useDebugPanelState({ params });

  const {
    position: position2,
    isDragging,
    panelRef,
    panelSize,
    handleMouseDown,
    handleResizeStart
  } = useDebugPanelPosition();

  const {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  } = useDebugPanelFiles({
    params,
    customUrl,
    showMode,
    position,
    refreshInterval,
    backgroundColor,
    caption,
    captionPosition,
    captionSize,
    captionColor,
    captionFont,
    captionBgColor,
    captionBgOpacity,
    transition,
    setCopied
  });

  const { 
    insertMetadataTag: getMetadataTagHandler,
    insertAllMetadata: getAllMetadataHandler
  } = useDebugPanelCaption({
    caption,
    metadataEntries,
    setPreviewCaption,
    onApplyCaption,
    imageUrl
  });

  const {
    handleRefreshMetadata
  } = useDebugPanelMetadata({
    imageUrl,
    metadata,
    setMetadataEntries
  });

  // Apply settings when display settings change
  useEffect(() => {
    if (imageUrl) {
      console.log('[useDebugPanel] Applying caption with preview settings');
      onApplyCaption(previewCaption);
    }
  }, [
    showMode, 
    position, 
    backgroundColor, 
    captionPosition, 
    captionSize, 
    captionColor, 
    captionFont, 
    captionBgColor, 
    captionBgOpacity, 
    previewCaption,
    onApplyCaption,
    imageUrl
  ]);

  // Create the actual handler functions (converting the returned functions to handlers)
  const insertMetadataTag = (key: string) => {
    const handler = getMetadataTagHandler(key);
    const newCaption = handler();
    if (newCaption) {
      setCaption(newCaption);
    }
  };

  const insertAllMetadata = () => {
    const newCaption = getAllMetadataHandler();
    setCaption(newCaption);
  };

  // This function adapts selectFile to be used directly (no need to call it again)
  const selectFileHandler = (file: string) => {
    const handler = selectFile(file);
    handler();
  };

  // This function adapts isCurrentFile to include imageUrl
  const isCurrentFileHandler = (file: string) => {
    return isCurrentFile(file, imageUrl);
  };

  return {
    activeTab,
    setActiveTab,
    customUrl,
    setCustomUrl,
    showMode,
    setShowMode,
    position,
    setPosition,
    refreshInterval, 
    setRefreshInterval,
    backgroundColor,
    setBackgroundColor,
    caption,
    setCaption,
    captionPosition,
    setCaptionPosition,
    captionSize,
    setCaptionSize,
    captionColor,
    setCaptionColor,
    captionFont,
    setCaptionFont,
    captionBgColor,
    setCaptionBgColor,
    captionBgOpacity,
    setCaptionBgOpacity,
    transition,
    setTransition,
    copied,
    metadataEntries,
    previewCaption,
    position2,
    isDragging,
    panelRef,
    panelSize,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile: selectFileHandler,
    formatFileName,
    resetSettings,
    insertMetadataTag,
    isCurrentFile: isCurrentFileHandler,
    formatTime,
    insertAllMetadata,
    handleMouseDown,
    handleResizeStart,
    handleRefreshMetadata
  };
};
