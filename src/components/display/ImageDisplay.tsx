
import React, { useState, useEffect } from 'react';
import { DisplayParams } from './types';
import { useNavigate } from 'react-router-dom';
import { createUrlWithParams } from './utils';
import { CaptionRenderer } from './debug/CaptionRenderer';

interface ImageDisplayProps {
  params: DisplayParams;
  imageUrl: string | null;
  imageKey: number;
  imageStyle: React.CSSProperties;
  processedCaption: string | null;
  metadata: Record<string, string>;
  isTransitioning: boolean;
  oldImageUrl: string | null;
  oldImageStyle: React.CSSProperties;
  newImageStyle: React.CSSProperties;
  imageRef: React.RefObject<HTMLImageElement>;
  onImageError: () => void;
}

export const ImageDisplay: React.FC<ImageDisplayProps> = ({
  params,
  imageUrl,
  imageKey,
  imageStyle,
  processedCaption,
  metadata,
  isTransitioning,
  oldImageUrl,
  oldImageStyle,
  newImageStyle,
  imageRef,
  onImageError
}) => {
  const navigate = useNavigate();
  const [containerSize, setContainerSize] = useState({ width: window.innerWidth, height: window.innerHeight });

  // Update container size on window resize
  useEffect(() => {
    const handleResize = () => {
      setContainerSize({
        width: window.innerWidth,
        height: window.innerHeight
      });
    };
    
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const handleDoubleClick = (e: React.MouseEvent) => {
    // Prevent event bubbling
    e.stopPropagation();
    e.preventDefault();
    
    // Log that we're trying to navigate to debug mode
    console.log('[ImageDisplay] Double-click detected, navigating to debug mode');
    
    // Create URL with existing params plus debug mode
    const newParams = { ...params, debugMode: true };
    const debugUrl = createUrlWithParams(newParams);
    
    // Navigate to the debug URL
    navigate(debugUrl);
  };

  // Metadata display styles
  const metadataStyle: React.CSSProperties = {
    position: 'absolute',
    padding: '12px',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
    color: '#ffffff',
    fontFamily: 'monospace',
    fontSize: '14px',
    maxWidth: '350px',
    borderRadius: '4px',
    top: '20px',
    left: '20px',
    zIndex: 10,
    overflowY: 'auto',
    maxHeight: '80vh',
  };

  // Get dimensions for scaling captions appropriately
  const imageDimensions = {
    width: imageRef.current?.naturalWidth || 0,
    height: imageRef.current?.naturalHeight || 0
  };
  
  // Fix background color handling - ensure it has proper format
  const captionBgColor = params.captionBgColor || '#000000';
  const formattedBgColor = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;

  return (
    <div 
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDoubleClick={handleDoubleClick}
    >
      {imageUrl && (
        <>
          <img
            key={imageKey}
            ref={imageRef}
            src={imageUrl}
            alt=""
            style={isTransitioning ? newImageStyle : imageStyle}
            onError={onImageError}
          />
          
          {/* Transitioning old image (for fades) */}
          {isTransitioning && oldImageUrl && (
            <img
              src={oldImageUrl}
              alt=""
              style={oldImageStyle}
            />
          )}
          
          {processedCaption && (
            <CaptionRenderer
              caption={processedCaption}
              position={params.captionPosition || 'bottom-center'}
              fontSize={params.captionSize || '16px'}
              color={params.captionColor || 'ffffff'}
              fontFamily={params.captionFont || 'Arial, sans-serif'}
              backgroundColor={formattedBgColor}
              backgroundOpacity={params.captionBgOpacity !== undefined ? params.captionBgOpacity : 0.7}
              containerWidth={containerSize.width}
              screenWidth={window.innerWidth}
              screenSize={imageDimensions}
            />
          )}
          
          {params.data !== undefined && Object.keys(metadata).length > 0 && (
            <div style={metadataStyle}>
              {Object.entries(metadata).map(([key, value]) => (
                <div key={key} style={{ margin: '4px 0' }}>
                  <strong>{key}:</strong> {value}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};
