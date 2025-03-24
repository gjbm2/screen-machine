
import React, { useEffect } from 'react';
import { Card } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import { DebugPanelHeader } from './DebugPanelHeader';
import { DebugPanelFooter } from './DebugPanelFooter';
import { DebugPanelTabs } from './DebugPanelTabs';
import { DebugPanelContent } from './DebugPanelContent';
import { useDebugPanel } from '../hooks/useDebugPanel';
import { DisplayParams } from '../types';

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: Date | null; 
  imageKey: number;
  outputFiles: string[];
  imageChanged?: boolean;
  onCheckNow: () => void;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
  onFocus?: () => void;
  isFixedPanel?: boolean;
}

export const DebugPanel: React.FC<DebugPanelProps> = ({
  params,
  imageUrl,
  lastModified,
  lastChecked,
  nextCheckTime,
  imageKey,
  outputFiles,
  imageChanged,
  onCheckNow,
  metadata,
  onApplyCaption,
  onFocus,
  isFixedPanel = false
}) => {
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
    selectFile,
    formatFileName,
    resetSettings,
    insertMetadataTag,
    isCurrentFile,
    formatTime,
    insertAllMetadata,
    handleMouseDown,
    handleResizeStart,
    handleRefreshMetadata
  } = useDebugPanel({ params, imageUrl, metadata, onApplyCaption });

  // Only use dragging functionality when not in fixed mode
  const handlePanelMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isFixedPanel) return;
    
    // Call the parent's focus handler to raise z-index
    if (onFocus) {
      onFocus();
    }
    // Call the original mouse down handler
    handleMouseDown(e);
  };

  const cardStyles = isFixedPanel 
    ? "h-full w-full overflow-auto" 
    : "absolute z-10 opacity-90 hover:opacity-100 transition-opacity shadow-lg min-w-[400px] min-h-[400px] resizable-container";

  const innerStyles = isFixedPanel 
    ? {} 
    : { 
        left: `${position2.x}px`, 
        top: `${position2.y}px`,
        width: panelSize.width,
        height: panelSize.height,
        cursor: isDragging ? 'grabbing' : 'auto',
        resize: 'none',
        position: 'absolute',
      };

  return (
    <Card 
      ref={panelRef}
      className={cardStyles}
      style={{ 
        ...innerStyles,
        display: 'flex',
        flexDirection: 'column'
      }}
      onMouseDown={handlePanelMouseDown}
    >
      <DebugPanelHeader 
        onCheckNow={onCheckNow}
        copyUrl={copyUrl}
        resetDisplay={resetDisplay}
        copied={copied}
      />
      
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full flex-1 flex flex-col">
        <DebugPanelTabs activeTab={activeTab} />
        
        <DebugPanelContent 
          activeTab={activeTab}
          outputFiles={outputFiles}
          imageChanged={imageChanged}
          imageUrl={imageUrl}
          customUrl={customUrl}
          setCustomUrl={setCustomUrl}
          selectFile={selectFile}
          isCurrentFile={isCurrentFile}
          formatFileName={formatFileName}
          showMode={showMode}
          position={position}
          refreshInterval={refreshInterval}
          backgroundColor={backgroundColor}
          transition={transition}
          setShowMode={setShowMode}
          setPosition={setPosition}
          setRefreshInterval={setRefreshInterval}
          setBackgroundColor={setBackgroundColor}
          setTransition={setTransition}
          resetSettings={resetSettings}
          metadataEntries={metadataEntries}
          insertMetadataTag={insertMetadataTag}
          setActiveTab={setActiveTab}
          onRefreshMetadata={handleRefreshMetadata}
          caption={caption}
          previewCaption={previewCaption}
          captionPosition={captionPosition}
          captionSize={captionSize}
          captionColor={captionColor}
          captionFont={captionFont}
          captionBgColor={captionBgColor}
          captionBgOpacity={captionBgOpacity}
          setCaption={setCaption}
          setCaptionPosition={setCaptionPosition}
          setCaptionSize={setCaptionSize}
          setCaptionColor={setCaptionColor}
          setCaptionFont={setCaptionFont}
          setCaptionBgColor={setCaptionBgColor}
          setCaptionBgOpacity={setCaptionBgOpacity}
          insertAllMetadata={insertAllMetadata}
        />
      </Tabs>
      
      <DebugPanelFooter 
        lastChecked={lastChecked}
        applySettings={applySettings}
        commitSettings={commitSettings}
        formatTime={formatTime}
      />
      
      {!isFixedPanel && <div className="resize-handle" onMouseDown={handleResizeStart} />}
    </Card>
  );
};
