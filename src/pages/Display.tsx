
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

type ShowMode = 'fit' | 'fill' | 'actual';

const Display = () => {
  const [searchParams] = useSearchParams();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  // Parse URL parameters
  const output = searchParams.get('output');
  const showMode = (searchParams.get('show') || 'fit') as ShowMode;
  const refreshInterval = Number(searchParams.get('refresh') || '5');
  const backgroundColor = searchParams.get('background') || '000000';

  // Function to validate and process the output parameter
  const processOutputParam = (outputParam: string | null): string | null => {
    if (!outputParam) return null;
    
    // Check if it's an absolute URL
    if (outputParam.startsWith('http://') || outputParam.startsWith('https://')) {
      return outputParam;
    }
    
    // Otherwise, treat as relative path from /output/
    return `/output/${outputParam}`;
  };

  // Function to check if the image has been modified
  const checkImageModified = async (url: string) => {
    try {
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      // Only update the image if the last-modified header has changed
      if (lastModified && lastModified !== lastModifiedRef.current) {
        lastModifiedRef.current = lastModified;
        setImageKey(prev => prev + 1);
      }
    } catch (err) {
      // Silent fail - continue showing the current image
      console.error('Error checking image modification:', err);
    }
  };

  // Initialize on first render
  useEffect(() => {
    if (!output) {
      setError('Error: "output" parameter is required. Usage: /display?output=image.jpg&show=fit&refresh=5&background=000000');
      return;
    }

    const processedUrl = processOutputParam(output);
    if (processedUrl) {
      setImageUrl(processedUrl);
      
      // Set up periodic checking for image changes
      intervalRef.current = window.setInterval(() => {
        if (processedUrl) {
          checkImageModified(processedUrl);
        }
      }, refreshInterval * 1000);
    }

    return () => {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [output, refreshInterval]);

  // Helper to handle image errors
  const handleImageError = () => {
    // Don't set error state, just log - we want to keep showing the last successful image
    console.error('Failed to load image:', imageUrl);
  };

  // Generate styles based on parameters
  const containerStyle: React.CSSProperties = {
    backgroundColor: `#${backgroundColor}`,
    width: '100vw',
    height: '100vh',
    margin: 0,
    padding: 0,
    overflow: 'hidden',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
  };

  const imageStyle: React.CSSProperties = (() => {
    switch (showMode) {
      case 'fill':
        return {
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          position: 'absolute',
          top: 0,
          left: 0,
        };
      case 'fit':
        return {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
      case 'actual':
        return {
          width: 'auto',
          height: 'auto',
          objectFit: 'none',
        };
      default:
        return {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
    }
  })();

  // If there's no output parameter, show error message
  if (error) {
    return (
      <div style={{
        ...containerStyle,
        color: '#ffffff',
        flexDirection: 'column',
        fontSize: '16px',
        textAlign: 'center',
        padding: '20px',
      }}>
        <h1 style={{ marginBottom: '20px' }}>{error}</h1>
        <p>Parameters:</p>
        <ul style={{ listStyleType: 'none', padding: 0 }}>
          <li><strong>output</strong>: (required) Image to display (e.g., image.jpg or full URL)</li>
          <li><strong>show</strong>: (optional) Display mode - 'fit', 'fill', or 'actual' (default: 'fit')</li>
          <li><strong>refresh</strong>: (optional) Check for image updates every X seconds (default: 5)</li>
          <li><strong>background</strong>: (optional) Background color hexcode (default: 000000)</li>
        </ul>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      {imageUrl && (
        <img
          key={imageKey}
          ref={imageRef}
          src={imageUrl}
          alt=""
          style={imageStyle}
          onError={handleImageError}
        />
      )}
    </div>
  );
};

export default Display;
