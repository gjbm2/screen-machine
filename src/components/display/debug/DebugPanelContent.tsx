
import React from 'react';
import { FilesPanel } from './panels/FilesPanel';
import { SettingsPanel } from './panels/SettingsPanel';
import { MetadataPanel } from './panels/MetadataPanel';
import { CaptionPanel } from './panels/CaptionPanel';
import { ShowMode, PositionMode, CaptionPosition, TransitionType, MetadataEntry } from '../types';

interface DebugPanelContentProps {
  activeTab: string;
  outputFiles: string[];
  imageChanged?: boolean;
  imageUrl: string | null;
  customUrl: string;
  setCustomUrl: (url: string) => void;
  selectFile: (file: string) => void;
  isCurrentFile: (file: string) => boolean;
  formatFileName: (fileName: string) => string;
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number; 
  backgroundColor: string;
  transition: TransitionType;
  setShowMode: (mode: ShowMode) => void;
  setPosition: (position: PositionMode) => void;
  setRefreshInterval: (interval: number) => void;
  setBackgroundColor: (color: string) => void;
  setTransition: (transition: TransitionType) => void;
  resetSettings: () => void;
  metadataEntries: MetadataEntry[];
  insertMetadataTag: (key: string) => void;
  setActiveTab: (tab: string) => void;
  onRefreshMetadata: () => Promise<Record<string, string>>;
  caption: string | null;
  previewCaption: string | null;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  setCaption: (caption: string | null) => void;
  setCaptionPosition: (position: CaptionPosition) => void;
  setCaptionSize: (size: string) => void;
  setCaptionColor: (color: string) => void;
  setCaptionFont: (font: string) => void;
  setCaptionBgColor: (color: string) => void;
  setCaptionBgOpacity: (opacity: number) => void;
  insertAllMetadata: () => void;
  applySettings?: () => void;
}

const DebugPanelContent: React.FC<DebugPanelContentProps> = ({
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
  insertAllMetadata,
  applySettings
}) => {
  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {activeTab === 'files' && (
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
      )}
      
      {activeTab === 'settings' && (
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
          applySettings={applySettings}
        />
      )}
      
      {activeTab === 'metadata' && (
        <MetadataPanel 
          metadataEntries={metadataEntries}
          insertMetadataTag={insertMetadataTag}
          setActiveTab={setActiveTab}
          onRefreshMetadata={onRefreshMetadata}
        />
      )}
      
      {activeTab === 'caption' && (
        <CaptionPanel 
          caption={caption || ''}
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
          insertMetadataTag={insertMetadataTag}
          insertAllMetadata={insertAllMetadata}
          applySettings={applySettings}
        />
      )}
    </div>
  );
};

export default DebugPanelContent;
