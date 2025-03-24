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

  const selectFileHandler = (file: string) => {
    const handler = selectFile(file);
    handler();
  };

  const isCurrentFileHandler = (file: string) => {
    return isCurrentFile(file, imageUrl);
  };

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: panelRef.current?.offsetWidth || 480,
      height: panelRef.current?.offsetHeight || 600
    });
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      const newX = Math.max(0, e.clientX - dragOffset.x);
      const newY = Math.max(0, e.clientY - dragOffset.y);
      
      const maxX = window.innerWidth - (panelRef.current?.offsetWidth || 480);
      const maxY = window.innerHeight - (panelRef.current?.offsetHeight || 400);
      
      setPosition2({ 
        x: Math.min(newX, maxX), 
        y: Math.min(newY, maxY) 
      });
    }
    
    if (isResizing) {
      const MIN_WIDTH = 400;
      const MIN_HEIGHT = 400;
      
      const newWidth = Math.max(MIN_WIDTH, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(MIN_HEIGHT, resizeStart.height + (e.clientY - resizeStart.y));
      
      const maxWidth = window.innerWidth - position2.x;
      const maxHeight = window.innerHeight - position2.y;
      
      setPanelSize({ 
        width: `${Math.min(newWidth, maxWidth)}px`, 
        height: `${Math.min(newHeight, maxHeight)}px` 
      });
    }
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
