
import React from 'react';
import { TabsContent } from "@/components/ui/tabs";
import { FilesPanel } from './panels/FilesPanel';
import { SettingsPanel } from './panels/SettingsPanel';
import { MetadataPanel } from './panels/MetadataPanel';
import { CaptionPanel } from './panels/CaptionPanel';
import { ShowMode, PositionMode, CaptionPosition, TransitionType } from '../types';

interface DebugPanelContentProps {
  activeTab: string;
  outputFiles: string[];
  imageChanged?: boolean;
  imageUrl: string | null;
  customUrl: string;
  setCustomUrl: (url: string) => void;
  selectFile: (file: string) => void;
  isCurrentFile: (file: string) => boolean;
  formatFileName: (file: string) => string;
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  transition: TransitionType;
  setShowMode: (value: ShowMode) => void;
  setPosition: (value: PositionMode) => void;
  setRefreshInterval: (value: number) => void;
  setBackgroundColor: (value: string) => void;
  setTransition: (value: TransitionType) => void;
  resetSettings: () => void;
  metadataEntries: Array<{key: string, value: string}>;
  insertMetadataTag: (key: string) => void;
  setActiveTab: (tab: string) => void;
  onRefreshMetadata: () => Promise<Record<string, string>>;
  caption: string;
  previewCaption: string | null;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  setCaption: (value: string) => void;
  setCaptionPosition: (value: CaptionPosition) => void;
  setCaptionSize: (value: string) => void;
  setCaptionColor: (value: string) => void;
  setCaptionFont: (value: string) => void;
  setCaptionBgColor: (value: string) => void;
  setCaptionBgOpacity: (value: number) => void;
  insertAllMetadata: () => void;
}

export const DebugPanelContent: React.FC<DebugPanelContentProps> = ({
  activeTab,
  outputFiles,
  imageChanged,
  imageUrl,
  customUrl,
  setCustomUrl,
  selectFile,
  isCurrentFile,
  formatFileName,
  showMode,
  position,
  refreshInterval,
  backgroundColor,
  transition,
  setShowMode,
  setPosition,
  setRefreshInterval,
  setBackgroundColor,
  setTransition,
  resetSettings,
  metadataEntries,
  insertMetadataTag,
  setActiveTab,
  onRefreshMetadata,
  caption,
  previewCaption,
  captionPosition,
  captionSize,
  captionColor,
  captionFont,
  captionBgColor,
  captionBgOpacity,
  setCaption,
  setCaptionPosition,
  setCaptionSize,
  setCaptionColor,
  setCaptionFont,
  setCaptionBgColor,
  setCaptionBgOpacity,
  insertAllMetadata
}) => {
  return (
    <>
      <TabsContent value="files" className="mt-0 flex-1 overflow-hidden">
        <FilesPanel 
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
        <SettingsPanel 
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
        <MetadataPanel 
          metadataEntries={metadataEntries}
          insertMetadataTag={insertMetadataTag}
          setActiveTab={setActiveTab}
          onRefreshMetadata={onRefreshMetadata}
        />
      </TabsContent>
      
      <TabsContent value="caption" className="mt-0 flex-1 overflow-hidden">
        <CaptionPanel 
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
    </>
  );
};
