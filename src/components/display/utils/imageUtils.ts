
// Cache mechanism for output files
const outputFilesCache = {
  files: [] as string[],
  timestamp: 0,
  expiryTime: 30000 // 30 seconds cache validity
};

// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // Check cache first with longer expiry time
    const now = Date.now();
    if (outputFilesCache.files.length > 0 && 
        (now - outputFilesCache.timestamp) < outputFilesCache.expiryTime) {
      console.log("[fetchOutputFiles] Using cached output files list, expires in", 
        Math.ceil((outputFilesCache.expiryTime - (now - outputFilesCache.timestamp))/1000), "seconds");
      return outputFilesCache.files;
    }
    
    console.log("[fetchOutputFiles] Fetching fresh output files from API");
    const response = await fetch('/api/output-files', {
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache'
      }
    });
    
    if (response.ok) {
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        const data = await response.json();
        const files = data.files || [];
        
        // Only update cache if files are different
        if (JSON.stringify(files) !== JSON.stringify(outputFilesCache.files)) {
          // Update cache
          outputFilesCache.files = files;
          outputFilesCache.timestamp = now;
          
          // Only log if we actually got files or it's the first request
          if (files.length > 0 || outputFilesCache.timestamp === 0) {
            console.log("[fetchOutputFiles] Successfully fetched output files:", files);
          }
        } else {
          console.log("[fetchOutputFiles] Fetched files identical to cache, no update needed");
        }
        
        return files;
      } else {
        console.warn('[fetchOutputFiles] Invalid content type from API:', contentType);
        return outputFilesCache.files; // Return cached data on failure
      }
    }
    
    console.warn('[fetchOutputFiles] Could not fetch output files from API, status:', response.status);
    return outputFilesCache.files; // Return cached data on failure
  } catch (e) {
    console.error('[fetchOutputFiles] Error fetching output files:', e);
    return outputFilesCache.files; // Return cached data on error
  }
};

// Check image dimensions (if possible)
export const getImageDimensions = async (url: string): Promise<{ width: number, height: number } | null> => {
  // Skip if URL is empty
  if (!url) {
    return null;
  }
  
  // Add logging to debug image loading issues
  console.log('[getImageDimensions] Attempting to load image:', url);
  
  // Check if URL is properly formatted
  if (!url) {
    console.error('[getImageDimensions] Invalid URL provided:', url);
    return null;
  }
  
  // Ensure URL has a leading slash if it's a relative path
  const formattedUrl = url.startsWith('/') || url.startsWith('http') ? url : `/${url}`;
  
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      console.log('[getImageDimensions] Successfully loaded image dimensions:', 
        img.naturalWidth, 'x', img.naturalHeight);
      resolve({
        width: img.naturalWidth,
        height: img.naturalHeight
      });
    };
    img.onerror = (error) => {
      console.error('[getImageDimensions] Failed to load image:', formattedUrl, 'Error:', error);
      resolve(null);
    };
    img.src = formattedUrl;
  });
};

// Calculate next check time
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number): Date | null => {
  if (!lastChecked) return null;
  return new Date(lastChecked.getTime() + refreshInterval * 1000);
};
