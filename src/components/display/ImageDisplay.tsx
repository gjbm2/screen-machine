
import React, { useState, useEffect } from 'react';
import { DisplayParams } from './types';
import { useNavigate } from 'react-router-dom';
import { createUrlWithParams } from './utils';

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

  const handleDoubleClick = () => {
    // Create URL with existing params plus debug mode
    const newParams = { ...params, debugMode: true };
    const debugUrl = createUrlWithParams(newParams);
    navigate(debugUrl);
  };

  // Calculate caption font size scaling
  const getScaledFontSize = (baseSize: string) => {
    // Extract numeric portion and unit
    const matches = baseSize.match(/^(\d+(?:\.\d+)?)([a-z%]+)?$/i);
    if (!matches) return baseSize;
    
    const size = parseFloat(matches[1]);
    const unit = matches[2] || 'px';
    
    // Base scaling on container width (viewport width)
    // This provides a good way to scale caption text for different screen sizes
    const baseWidth = 1920; // Reference width (Full HD)
    const scaleFactor = containerSize.width / baseWidth;
    
    // Apply scaling but limit to reasonable bounds
    const scaledSize = Math.max(10, Math.min(96, size * scaleFactor)); // Increased max size to 96px
    
    return `${scaledSize}${unit}`;
  };

  // Caption styles
  const captionStyle: React.CSSProperties = (() => {
    if (!processedCaption) return {};

    const scaledFontSize = getScaledFontSize(params.captionSize || '16px');

    // Fix background color handling - ensure it has proper format
    const bgColor = params.captionBgColor || '#000000';
    const formattedBgColor = bgColor.startsWith('#') ? bgColor : `#${bgColor}`;
    
    // Calculate background opacity as hex value for rgba
    const bgOpacityValue = params.captionBgOpacity !== undefined ? params.captionBgOpacity : 0.7;
    const bgOpacityHex = Math.round(bgOpacityValue * 255).toString(16).padStart(2, '0');
    
    // Combine color and opacity
    const bgColorWithOpacity = `${formattedBgColor}${bgOpacityHex}`;

    console.log('[ImageDisplay] Caption styles:', {
      bgColor,
      formattedBgColor,
      bgOpacityValue,
      bgOpacityHex,
      bgColorWithOpacity,
      fontSize: scaledFontSize
    });

    const styles: React.CSSProperties = {
      position: 'absolute',
      padding: '8px 16px',
      backgroundColor: bgColorWithOpacity,
      color: `#${params.captionColor || 'ffffff'}`,
      fontSize: scaledFontSize,
      fontFamily: params.captionFont || 'Arial, sans-serif',
      maxWidth: '80%',
      textAlign: 'center',
      borderRadius: '4px',
      zIndex: 10,
      whiteSpace: processedCaption.includes('\n') ? 'pre-line' : 'normal',
    };

    switch (params.captionPosition) {
      case 'top-left':
        styles.top = '20px';
        styles.left = '20px';
        break;
      case 'top-center':
        styles.top = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        styles.top = '20px';
        styles.right = '20px';
        break;
      case 'bottom-left':
        styles.bottom = '20px';
        styles.left = '20px';
        break;
      case 'bottom-center':
        styles.bottom = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        styles.bottom = '20px';
        styles.right = '20px';
        break;
      default:
        styles.bottom = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
    }

    return styles;
  })();

  // Metadata display styles
  const metadataStyle: React.CSSProperties = {
    position: 'absolute',
    padding: '12px',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
    color: '#ffffff',
    fontFamily: 'monospace',
    fontSize: getScaledFontSize('14px'),
    maxWidth: '350px',
    borderRadius: '4px',
    top: '20px',
    left: '20px',
    zIndex: 10,
    overflowY: 'auto',
    maxHeight: '80vh',
  };

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
            <div style={captionStyle}>
              {processedCaption}
            </div>
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
