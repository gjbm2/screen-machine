
import React from 'react';
import { DebugPanel } from '@/components/display/debug/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ImageDisplay } from '@/components/display/ImageDisplay';
import { DisplayParams } from './types';

interface DisplayModeProps {
  params: DisplayParams;
  previewParams: DisplayParams;
  imageUrl: string | null;
  imageKey: number;
  imageRef: React.RefObject<HTMLImageElement>;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: Date | null;  // Ensure this is Date | null
  imageChanged: boolean;
  outputFiles: string[];
  metadata: Record<string, string>;
  processedCaption: string | null;
  isTransitioning: boolean;
  oldImageUrl: string | null;
  oldImageStyle: React.CSSProperties;
  newImageStyle: React.CSSProperties;
  onHandleManualCheck: () => void;
  onImageError: () => void;
  getImagePositionStyle: (position: DisplayParams['position'], showMode: DisplayParams['showMode'], containerWidth: number, containerHeight: number, imageWidth: number, imageHeight: number) => React.CSSProperties;
}

export const DisplayMode: React.FC<DisplayModeProps> = ({
  params,
  previewParams,
  imageUrl,
  imageKey,
  imageRef,
  lastModified,
  lastChecked,
  nextCheckTime,
  imageChanged,
  outputFiles,
  metadata,
  processedCaption,
  isTransitioning,
  oldImageUrl,
  oldImageStyle,
  newImageStyle,
  onHandleManualCheck,
  onImageError,
  getImagePositionStyle
}) => {
  const imageStyle = getImagePositionStyle(
    previewParams.position,
    previewParams.showMode,
    window.innerWidth,
    window.innerHeight,
    imageRef.current?.naturalWidth || 0,
    imageRef.current?.naturalHeight || 0
  );

  if (params.debugMode) {
    return (
      <>
        <DebugPanel 
          params={params}
          imageUrl={imageUrl}
          lastModified={lastModified}
          lastChecked={lastChecked}
          nextCheckTime={nextCheckTime}  // Pass as Date | null
          imageKey={imageKey}
          outputFiles={outputFiles}
          imageChanged={imageChanged}
          onCheckNow={onHandleManualCheck}
          metadata={metadata}
          onApplyCaption={(caption) => {}}
        />
        <DebugImageContainer 
          imageUrl={imageUrl}
          imageKey={imageKey}
          showMode={previewParams.showMode}
          position={previewParams.position}
          backgroundColor={previewParams.backgroundColor}
          onImageError={onImageError}
          imageRef={imageRef}
          imageChanged={imageChanged}
          caption={processedCaption}
          captionPosition={previewParams.captionPosition}
          captionSize={previewParams.captionSize}
          captionColor={previewParams.captionColor}
          captionFont={previewParams.captionFont}
          captionBgColor={previewParams.captionBgColor}
          captionBgOpacity={previewParams.captionBgOpacity}
          metadata={metadata}
          onSettingsChange={() => {}} 
        />
      </>
    );
  }

  return (
    <ImageDisplay
      params={previewParams}
      imageUrl={imageUrl}
      imageKey={imageKey}
      imageStyle={imageStyle}
      processedCaption={processedCaption}
      metadata={metadata}
      isTransitioning={isTransitioning}
      oldImageUrl={oldImageUrl}
      oldImageStyle={oldImageStyle}
      newImageStyle={newImageStyle}
      imageRef={imageRef}
      onImageError={onImageError}
    />
  );
};
