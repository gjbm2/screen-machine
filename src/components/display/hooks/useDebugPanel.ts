
import { DisplayParams } from '../types';
import { useDebugPanelState } from './useDebugPanelState';
import { useDebugPanelFiles } from './useDebugPanelFiles';
import { useDebugPanelPosition } from './useDebugPanelPosition';
import { useDebugPanelConfiguration } from './useDebugPanelConfiguration';

interface DebugPanelHookProps {
  params: DisplayParams;
  imageUrl: string | null;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
}

export const useDebugPanel = ({ params, imageUrl, metadata, onApplyCaption }: DebugPanelHookProps) => {
  // Core state management
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

  // Panel position and resize management
  const {
    position: position2,
    isDragging,
    panelRef,
    panelSize,
    handleMouseDown,
    handleResizeStart,
    isResizing,
    setIsResizing,
    resizeStart,
    setResizeStart,
    dragOffset,
    setPosition: setPosition2,
    setPanelSize
  } = useDebugPanelPosition();

  // Files and URLs management
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

  // Caption, metadata, and config related functionality
  const {
    insertMetadataTag,
    insertAllMetadata,
    selectFileHandler,
    isCurrentFileHandler,
    handleRefreshMetadata,
    handleResizeStartInternal
  } = useDebugPanelConfiguration({
    caption,
    setCaption,
    selectFile,
    isCurrentFile,
    imageUrl,
    metadata,
    setMetadataEntries,
    previewCaption,
    setPreviewCaption,
    onApplyCaption,
    handleResizeStart
  });

  return {
    // State
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
    
    // Position and sizing
    position2,
    isDragging,
    panelRef,
    panelSize,
    
    // Actions
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile, // Use direct selectFile, not the wrapped handler for prop passing
    formatFileName,
    resetSettings,
    insertMetadataTag,
    isCurrentFile, // Use direct isCurrentFile, not the wrapped handler for prop passing
    formatTime,
    insertAllMetadata,
    handleMouseDown,
    handleResizeStart: handleResizeStartInternal,
    handleRefreshMetadata
  };
};
