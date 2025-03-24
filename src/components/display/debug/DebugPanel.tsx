
import React from 'react';
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { DebugPanelHeader } from './DebugPanelHeader';
import { DebugPanelFooter } from './DebugPanelFooter';
import { DebugPanelTabs } from './DebugPanelTabs';
import { FilesTab } from './FilesTab';
import { SettingsTab } from './SettingsTab';
import { MetadataTab } from './MetadataTab';
import { CaptionTab } from './CaptionTab';
import { ResizeHandle } from './ResizeHandle';
import { useDebugPanel } from './useDebugPanel';
import { DisplayParams } from '../types';

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: Date | null; // Ensure this is Date | null
  imageKey: number;
  outputFiles: string[];
  imageChanged?: boolean;
  onCheckNow: () => void;
  metadata: Record<string, string>;
  onApplyCaption: (caption: string | null) => void;
}

export const DebugPanel: React.FC<DebugPanelProps> = ({
  params,
  imageUrl,
  lastModified,
  lastChecked,
  nextCheckTime, // This is Date | null
  imageKey,
  outputFiles,
  imageChanged,
  onCheckNow,
  metadata,
  onApplyCaption
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
    handleResizeStart
  } = useDebugPanel({ params, imageUrl, metadata, onApplyCaption });

  return (
    <Card 
      ref={panelRef}
      className="absolute z-10 opacity-90 hover:opacity-100 transition-opacity shadow-lg"
      style={{ 
        left: `${position2.x}px`, 
        top: `${position2.y}px`,
        width: panelSize.width,
        height: panelSize.height,
        cursor: isDragging ? 'grabbing' : 'auto',
        resize: 'none',
        position: 'absolute',
        display: 'flex',
        flexDirection: 'column'
      }}
      onMouseDown={handleMouseDown}
    >
      <DebugPanelHeader 
        onCheckNow={onCheckNow}
        copyUrl={copyUrl}
        resetDisplay={resetDisplay}
        copied={copied}
      />
      
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full flex-1 flex flex-col">
        <DebugPanelTabs activeTab={activeTab} />
        
        <TabsContent value="files" className="mt-0 flex-1 overflow-hidden">
          <FilesTab 
            outputFiles={outputFiles}
            imageChanged={imageChanged}
            imageUrl={imageUrl}
            customUrl={customUrl}
            setCustomUrl={setCustomUrl}
            selectFile={selectFile}
            isCurrentFile={isCurrentFile}
            formatFileName={formatFileName}
          />
        </TabsContent>
        
        <TabsContent value="settings" className="mt-0 flex-1 overflow-auto">
          <SettingsTab 
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
          />
        </TabsContent>
        
        <TabsContent value="metadata" className="mt-0 flex-1 overflow-hidden">
          <MetadataTab 
            metadataEntries={metadataEntries}
            insertMetadataTag={insertMetadataTag}
            setActiveTab={setActiveTab}
          />
        </TabsContent>
        
        <TabsContent value="caption" className="mt-0 flex-1 overflow-hidden">
          <CaptionTab 
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
        </TabsContent>
      </Tabs>
      
      <DebugPanelFooter 
        lastChecked={lastChecked}
        applySettings={applySettings}
        commitSettings={commitSettings}
        formatTime={formatTime}
      />
      
      <ResizeHandle onMouseDown={handleResizeStart} />
    </Card>
  );
};
