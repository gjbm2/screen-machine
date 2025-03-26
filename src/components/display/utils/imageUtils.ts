
// Cache mechanism for output files
const outputFilesCache = {
  files: [] as string[],
  timestamp: 0,
  expiryTime: 5000 // 5 seconds cache validity
};

// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // Check cache first
    const now = Date.now();
    if (outputFilesCache.files.length > 0 && 
        (now - outputFilesCache.timestamp) < outputFilesCache.expiryTime) {
      console.log("[fetchOutputFiles] Using cached output files list");
      return outputFilesCache.files;
    }
    
    console.log("[fetchOutputFiles] Fetching fresh output files from API");
    const response = await fetch('/api/output-files');
    
    if (response.ok) {
      const data = await response.json();
      const files = data.files || [];
      
      // Update cache
      outputFilesCache.files = files;
      outputFilesCache.timestamp = now;
      
      // Only log if we actually got files or it's the first request
      if (files.length > 0 || outputFilesCache.timestamp === 0) {
        console.log("[fetchOutputFiles] Successfully fetched output files:", files);
      }
      
      return files;
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
