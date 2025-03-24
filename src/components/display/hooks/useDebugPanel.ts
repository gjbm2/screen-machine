
import { useState, useEffect } from 'react';
import { DisplayParams } from '../types';
import { useDebugPanelState } from './useDebugPanelState';
import { useDebugPanelFiles } from './useDebugPanelFiles';
import { useDebugPanelPosition } from './useDebugPanelPosition';
import { useDebugPanelCaption } from './useDebugPanelCaption';
import { useDebugPanelMetadata } from './useDebugPanelMetadata';
import { useDebugPanelPreview } from './useDebugPanelPreview';
import { useDebugPanelMetadataTag } from './useDebugPanelMetadataTag';
import { useDebugPanelFileSelector } from './useDebugPanelFileSelector';
import { useDebugPanelResize } from './useDebugPanelResize';

interface DebugPanelHookProps {
  params: DisplayParams;
  imageUrl: string | null;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
}

export const useDebugPanel = ({ params, imageUrl, metadata, onApplyCaption }: DebugPanelHookProps) => {
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
    handleResizeStart: positionResizeHandler,
    isResizing,
    setIsResizing,
    resizeStart,
    setResizeStart,
    dragOffset,
    setPosition: setPosition2,
    setPanelSize
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

  // Use our new hooks to organize functionality
  useDebugPanelPreview({
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
  });

  const {
    insertMetadataTag,
    insertAllMetadata
  } = useDebugPanelMetadataTag({
    caption,
    getMetadataTagHandler,
    getAllMetadataHandler,
    setCaption
  });

  const {
    selectFileHandler,
    isCurrentFileHandler
  } = useDebugPanelFileSelector({
    selectFile,
    isCurrentFile,
    imageUrl
  });

  const {
    handleResizeStartInternal
  } = useDebugPanelResize({
    positionResizeHandler
  });

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
    handleResizeStart: handleResizeStartInternal,
    handleRefreshMetadata
  };
};
