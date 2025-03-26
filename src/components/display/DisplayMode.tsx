import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
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
  isChecking?: boolean;
  isLoadingMetadata?: boolean;
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
  getImagePositionStyle,
  isChecking = false,
  isLoadingMetadata = false
}) => {
  const isMobile = useIsMobile();
  
  console.log('[DEBUG DisplayMode] Rendering with params:', {
    imageUrl,
    imageKey,
    params,
    previewParams
  });

  const [showPreview, setShowPreview] = useState(!isMobile);
  const previewContainerRef = useRef<HTMLDivElement>(null);
  const renderCountRef = useRef(0);
  const lastLogTimeRef = useRef(0);
  
  useEffect(() => {
    if (import.meta.env.DEV) {
      renderCountRef.current++;
      
      const now = Date.now();
      if (now - lastLogTimeRef.current > 10000) {
        console.log('[DisplayMode] Rendering (log limited, render #' + renderCountRef.current + ')');
        lastLogTimeRef.current = now;
      }
    }
  });
  
  useEffect(() => {
    if (isMobile) {
      setShowPreview(true);
    }
  }, [isMobile]);
  
  const toggleView = useCallback(() => {
    setShowPreview(prev => !prev);
  }, []);

  const debugPanel = useMemo(() => (
    <div className={`${isMobile ? (showPreview ? 'hidden' : 'w-full h-full') : 'w-2/5'} flex-shrink-0 overflow-hidden flex flex-col`}>
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
        isChecking={isChecking}
      />
    </div>
  ), [
    params, imageUrl, lastModified, lastChecked, nextCheckTime, imageKey, 
    outputFiles, imageChanged, onHandleManualCheck, metadata, 
    isMobile, showPreview, isChecking, toggleView
  ]);

  const previewPanel = useMemo(() => (
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
  ), [
    imageUrl, imageKey, previewParams, onImageError, imageRef, imageChanged, 
    processedCaption, metadata, toggleView, showPreview, isMobile
  ]);

  const mobileViewSwitcher = useMemo(() => (
    isMobile && (
      <div className="fixed top-3 right-3 z-[100]">
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
    )
  ), [isMobile, toggleView, showPreview]);

  const imageDisplay = useMemo(() => {
    console.log('[DEBUG DisplayMode] Creating imageDisplay component with:', {
      imageUrl,
      imageKey,
      isTransitioning,
      imageRefWidth: imageRef.current?.naturalWidth,
      imageRefHeight: imageRef.current?.naturalHeight
    });
    
    const imageStyle = getImagePositionStyle(
      previewParams.position,
      previewParams.showMode,
      window.innerWidth,
      window.innerHeight,
      imageRef.current?.naturalWidth || 0,
      imageRef.current?.naturalHeight || 0
    );
    
    console.log('[DEBUG DisplayMode] Calculated image style:', imageStyle);
    
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
        isLoadingMetadata={isLoadingMetadata}
      />
    );
  }, [
    previewParams, imageUrl, imageKey, getImagePositionStyle, processedCaption, 
    metadata, isTransitioning, oldImageUrl, oldImageStyle, newImageStyle, 
    imageRef, onImageError, isLoadingMetadata
  ]);

  if (params.debugMode) {
    console.log('[DEBUG DisplayMode] Rendering in debug mode');
    return (
      <div className="fixed inset-0 flex flex-col sm:flex-row overflow-hidden">
        {mobileViewSwitcher}
        {debugPanel}
        {previewPanel}
      </div>
    );
  }

  console.log('[DEBUG DisplayMode] Rendering normal image display with imageUrl:', imageUrl);
  return imageDisplay;
};
