
import React, { useEffect, useState } from 'react';
import { ShowMode, PositionMode } from '../../types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle } from "lucide-react";

interface ImageDisplayProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  onImageError: () => void;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  imageDimensions: { width: number; height: number };
  imageRef: React.RefObject<HTMLImageElement>;
  getImageStyle: () => React.CSSProperties;
}

export const ImageDisplay: React.FC<ImageDisplayProps> = ({
  imageUrl,
  imageKey,
  onImageError,
  onImageLoad,
  imageRef,
  getImageStyle
}) => {
  // Debug logging
  useEffect(() => {
    console.log('[DEBUG ImageDisplay] Component rendered with props:', { imageUrl, imageKey });
    if (imageUrl) {
      console.log('[DEBUG ImageDisplay] Image URL being rendered:', imageUrl);
      
      // Test if the image exists
      const testImg = new Image();
      testImg.onload = () => console.log('[DEBUG ImageDisplay] Test image verified to load:', imageUrl);
      testImg.onerror = (e) => console.error('[DEBUG ImageDisplay] Test image verified to FAIL loading:', imageUrl, e);
      testImg.src = imageUrl;
    }
  }, [imageUrl, imageKey]);

  const [hasError, setHasError] = useState(false);
  const [isImageVisible, setIsImageVisible] = useState(false);
  const [errorDetails, setErrorDetails] = useState<string>('');

  // Reset error state when image URL changes
  useEffect(() => {
    console.log('[DEBUG ImageDisplay] Image URL changed, resetting error state:', imageUrl);
    setHasError(false);
    setIsImageVisible(false);
    setErrorDetails('');
  }, [imageUrl]);

  // Additional check for image URL sanity
  useEffect(() => {
    if (imageUrl && typeof imageUrl === 'string') {
      console.log('[DEBUG ImageDisplay] Analyzing imageUrl:', {
        url: imageUrl,
        length: imageUrl.length,
        startsWithHttp: imageUrl.startsWith('http'),
        startsWithSlash: imageUrl.startsWith('/')
      });
      
      // Log different parts of a URL to ensure it's well-formed
      if (imageUrl.startsWith('http')) {
        try {
          const urlObj = new URL(imageUrl);
          console.log('[DEBUG ImageDisplay] URL parts:', {
            protocol: urlObj.protocol,
            hostname: urlObj.hostname,
            pathname: urlObj.pathname,
            search: urlObj.search
          });
        } catch (e) {
          console.error('[DEBUG ImageDisplay] Error parsing URL:', e);
        }
      }
    }
  }, [imageUrl]);

  if (!imageUrl) {
    console.log('[DEBUG ImageDisplay] No image URL provided, showing placeholder');
    return (
      <div className="w-full h-full flex items-center justify-center text-gray-500">
        No image to display
      </div>
    );
  }

  if (hasError) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <Alert variant="destructive" className="max-w-md">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            Failed to load image: {imageUrl}<br/>
            {errorDetails && <span className="text-xs block mt-1">{errorDetails}</span>}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  // Log the actual image URL we're trying to load
  console.log('[DEBUG ImageDisplay] Attempting to load image from URL:', imageUrl);
  console.log('[DEBUG ImageDisplay] Current image style:', getImageStyle());
  
  // Test if the image exists with fetch in dev mode
  if (import.meta.env.DEV) {
    fetch(imageUrl, { method: 'HEAD' })
      .then(response => {
        console.log('[DEBUG ImageDisplay] Image HEAD request status:', response.status, response.ok);
        if (!response.ok) {
          console.warn('[DEBUG ImageDisplay] Image might not exist:', response.status);
        }
      })
      .catch(err => {
        console.error('[DEBUG ImageDisplay] Network error checking image:', err);
      });
  }
  
  return (
    <img
      ref={imageRef}
      key={`image-${imageKey}`}
      src={imageUrl}
      alt="Preview"
      className={`max-w-full max-h-full ${isImageVisible ? 'opacity-100' : 'opacity-0'}`}
      onLoad={(e) => {
        console.log('[DEBUG ImageDisplay] Image loaded successfully:', imageUrl);
        setHasError(false);
        setIsImageVisible(true);
        onImageLoad(e);
      }}
      onError={(e) => {
        const target = e.target as HTMLImageElement;
        console.error('[DEBUG ImageDisplay] Error loading image:', imageUrl, e);
        setHasError(true);
        setIsImageVisible(false);
        setErrorDetails(`Source: ${target.src}, Complete: ${target.complete}`);
        onImageError();
      }}
      style={getImageStyle()}
    />
  );
};
