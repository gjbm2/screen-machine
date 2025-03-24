
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
import { toast } from 'sonner';
import { extractImageMetadata } from '../utils';

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

  // Handle manual metadata refresh
  const handleRefreshMetadata = async () => {
    console.log('[DebugPanel] handleRefreshMetadata called');
    if (!imageUrl) {
      toast.error("No image URL to extract metadata from");
      return {};
    }
    
    try {
      toast.info("Manually refreshing metadata...");
      // Add cache-busting parameter
      const cacheBustUrl = `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}manualRefresh=${Date.now()}`;
      console.log('[DebugPanel] Refreshing metadata for URL:', cacheBustUrl);
      
      // First try the direct API endpoint with fetch
      try {
        console.log('[DebugPanel] Trying direct API call to extract-metadata');
        const apiUrl = '/api/extract-metadata';
        const params = new URLSearchParams({ url: cacheBustUrl });
        const response = await fetch(`${apiUrl}?${params.toString()}`);
        
        if (!response.ok) {
          throw new Error(`API call failed: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('[DebugPanel] API response:', data);
        
        if (data && typeof data === 'object' && Object.keys(data).length > 0) {
          toast.success(`Found ${Object.keys(data).length} metadata entries`);
          // Force page refresh to update UI with new metadata
          window.location.reload();
          return data;
        }
        
        console.warn('[DebugPanel] API call returned no metadata or invalid format');
      } catch (apiErr) {
        console.error('[DebugPanel] Error in direct API call:', apiErr);
      }
      
      // Fallback to using the extractImageMetadata utility
      console.log('[DebugPanel] Falling back to extractImageMetadata utility');
      const newMetadata = await extractImageMetadata(cacheBustUrl);
      console.log('[DebugPanel] Metadata from utility:', newMetadata);
      
      if (Object.keys(newMetadata).length > 0) {
        toast.success(`Found ${Object.keys(newMetadata).length} metadata entries`);
        // Force page refresh to update UI with new metadata
        window.location.reload();
        return newMetadata;
      }
      
      toast.warning("No metadata found in this image");
      return {};
    } catch (err) {
      console.error('[DebugPanel] Error refreshing metadata:', err);
      toast.error("Failed to refresh metadata");
      return {};
    }
  };

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
            onRefreshMetadata={handleRefreshMetadata}
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
