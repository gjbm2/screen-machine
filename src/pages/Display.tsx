
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";

type ShowMode = 'fit' | 'fill' | 'actual';

const Display = () => {
  const [searchParams] = useSearchParams();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  // Parse URL parameters
  const output = searchParams.get('output');
  const showMode = (searchParams.get('show') || 'fit') as ShowMode;
  const refreshInterval = Number(searchParams.get('refresh') || '5');
  const backgroundColor = searchParams.get('background') || '000000';
  const debugMode = searchParams.get('debug') === 'true';

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

  // Function to fetch available output files
  const fetchOutputFiles = async () => {
    try {
      // This endpoint would need to be implemented on the server
      const response = await fetch('/api/output-files');
      if (response.ok) {
        const files = await response.json();
        setOutputFiles(files);
      } else {
        console.error('Failed to fetch output files');
        setOutputFiles([
          'sample.jpg',
          'image.png',
          'result.jpg'
        ]); // Fallback to demo values if endpoint isn't available
      }
    } catch (err) {
      console.error('Error fetching output files:', err);
      // Use demo values for now
      setOutputFiles([
        'sample.jpg',
        'image.png',
        'result.jpg'
      ]);
    }
  };

  // Function to check if the image has been modified
  const checkImageModified = async (url: string) => {
    try {
      // Record the check time
      setLastChecked(new Date());
      
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      // Update the last modified timestamp for debugging
      setLastModified(lastModified);
      
      // Only update the image if the last-modified header has changed
      if (lastModified && lastModified !== lastModifiedRef.current) {
        console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
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
      
      // Initial check for last-modified
      checkImageModified(processedUrl);
      
      // Set up periodic checking for image changes
      intervalRef.current = window.setInterval(() => {
        if (processedUrl) {
          checkImageModified(processedUrl);
        }
      }, refreshInterval * 1000);
    }

    // Fetch available output files if in debug mode
    if (debugMode) {
      fetchOutputFiles();
    }

    return () => {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [output, refreshInterval, debugMode]);

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

  // Format date for display
  const formatDateTime = (date: Date | null) => {
    if (!date) return 'N/A';
    return date.toLocaleTimeString();
  };

  // Calculate next check time
  const getNextCheckTime = () => {
    if (!lastChecked) return 'N/A';
    const nextCheck = new Date(lastChecked.getTime() + refreshInterval * 1000);
    return formatDateTime(nextCheck);
  };

  // Debug panel with all parameters and timestamp
  const DebugPanel = () => (
    <Card className="absolute top-4 left-4 z-10 w-96 bg-white/90 dark:bg-gray-800/90 shadow-lg">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Debug Information</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        <h3 className="font-bold mb-1">Parameters:</h3>
        <ul className="space-y-1 mb-3">
          <li><strong>output:</strong> {output}</li>
          <li><strong>show:</strong> {showMode}</li>
          <li><strong>refresh:</strong> {refreshInterval}s</li>
          <li><strong>background:</strong> #{backgroundColor}</li>
        </ul>
        <h3 className="font-bold mb-1">Image Info:</h3>
        <ul className="space-y-1 mb-3">
          <li><strong>URL:</strong> {imageUrl}</li>
          <li><strong>Last-Modified:</strong> {lastModified || 'Unknown'}</li>
          <li><strong>Last Checked:</strong> {formatDateTime(lastChecked)}</li>
          <li><strong>Next Check At:</strong> {getNextCheckTime()}</li>
          <li><strong>Image Key:</strong> {imageKey}</li>
        </ul>
        <h3 className="font-bold mb-1">Available Output Files:</h3>
        <div className="max-h-40 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded p-2">
          {outputFiles.length > 0 ? (
            <ul className="space-y-1">
              {outputFiles.map((file, index) => (
                <li key={index} className="hover:bg-gray-100 dark:hover:bg-gray-700 p-1 rounded">
                  <a 
                    href={`/display?output=${file}&show=${showMode}&refresh=${refreshInterval}&background=${backgroundColor}&debug=true`}
                    className="block text-blue-500 dark:text-blue-400 hover:underline"
                  >
                    {file}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500">No files found</p>
          )}
        </div>
      </CardContent>
    </Card>
  );

  // Debug mode image container
  const DebugImageContainer = () => {
    // Get the viewport dimensions to simulate the correct aspect ratio
    const viewportRatio = window.innerWidth / window.innerHeight;
    
    return (
      <Card className="w-2/3 max-w-3xl mx-auto">
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Image Preview ({showMode} mode)</CardTitle>
        </CardHeader>
        <CardContent>
          {/* Maintain aspect ratio of viewport */}
          <AspectRatio ratio={viewportRatio} className="overflow-hidden">
            <div 
              className="w-full h-full relative flex items-center justify-center"
              style={{ backgroundColor: `#${backgroundColor}` }}
            >
              {imageUrl && (
                <img
                  key={imageKey}
                  ref={imageRef}
                  src={imageUrl}
                  alt=""
                  style={{
                    ...(showMode === 'fill' ? {
                      width: '100%',
                      height: '100%',
                      objectFit: 'cover',
                      position: 'absolute',
                      top: 0,
                      left: 0,
                    } : showMode === 'fit' ? {
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                    } : {
                      width: 'auto',
                      height: 'auto',
                      objectFit: 'none',
                    })
                  }}
                  onError={handleImageError}
                />
              )}
            </div>
          </AspectRatio>
        </CardContent>
      </Card>
    );
  };

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
          <li><strong>debug</strong>: (optional) Show debug information (true or false, default: false)</li>
        </ul>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      {/* Debug mode */}
      {debugMode ? (
        <>
          <DebugPanel />
          <DebugImageContainer />
        </>
      ) : (
        <>
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
        </>
      )}
    </div>
  );
};

export default Display;
