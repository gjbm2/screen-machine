
import React, { useState, useEffect, useRef } from 'react';
import { DebugPanel } from '@/components/display/debug/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ImageDisplay } from '@/components/display/ImageDisplay';
import { DisplayParams } from './types';
import { useIsMobile } from '@/hooks/use-mobile';
import { Button } from '@/components/ui/button';
import { Eye, Settings } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface DisplayModeProps {
  params: DisplayParams;
  previewParams: DisplayParams;
  imageUrl: string | null;
  imageKey: number;
  imageRef: React.RefObject<HTMLImageElement>;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: Date | null;
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
  const isMobile = useIsMobile();
  const [showPreview, setShowPreview] = useState(!isMobile);
  const previewContainerRef = useRef<HTMLDivElement>(null);
  
  // When switching to mobile view, we want to show the preview by default
  useEffect(() => {
    if (isMobile) {
      setShowPreview(true);
    }
  }, [isMobile]);
  
  // Toggle preview/settings view on mobile
  const toggleView = () => {
    setShowPreview(prev => !prev);
  };

  if (params.debugMode) {
    return (
      <div className="fixed inset-0 flex flex-col sm:flex-row overflow-hidden">
        {/* Mobile View Switcher - fixed position */}
        {isMobile && (
          <div className="fixed top-3 right-3 z-50">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={toggleView}
                    className="h-8 w-8 p-0 bg-background shadow-md border-border"
                  >
                    {showPreview ? <Settings className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{showPreview ? "Show Settings" : "Show Preview"}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        )}
        
        {/* Settings Panel */}
        <div 
          className={`${isMobile ? (showPreview ? 'hidden' : 'w-full h-full') : 'w-2/5'} flex-shrink-0 overflow-hidden flex flex-col`}
        >
          <DebugPanel 
            params={params}
            imageUrl={imageUrl}
            lastModified={lastModified}
            lastChecked={lastChecked}
            nextCheckTime={nextCheckTime}
            imageKey={imageKey}
            outputFiles={outputFiles}
            imageChanged={imageChanged}
            onCheckNow={onHandleManualCheck}
            metadata={metadata}
            onApplyCaption={(caption) => {}}
            isFixedPanel={true}
            togglePreview={toggleView}
            showingPreview={showPreview}
            isMobile={isMobile}
          />
        </div>
        
        {/* Preview Panel */}
        <div 
          ref={previewContainerRef}
          className={`${isMobile ? (showPreview ? 'w-full h-full' : 'hidden') : 'w-3/5'} flex-grow bg-gray-100 dark:bg-gray-900 overflow-hidden flex flex-col`}
        >
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
            onSettingsChange={toggleView}
            isFixedPanel={true}
            togglePreview={toggleView}
            showingPreview={showPreview}
            isMobile={isMobile}
          />
        </div>
      </div>
    );
  }

  return (
    <ImageDisplay
      params={previewParams}
      imageUrl={imageUrl}
      imageKey={imageKey}
      imageStyle={getImagePositionStyle(
        previewParams.position,
        previewParams.showMode,
        window.innerWidth,
        window.innerHeight,
        imageRef.current?.naturalWidth || 0,
        imageRef.current?.naturalHeight || 0
      )}
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
