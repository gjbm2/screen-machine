// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    console.log("[fetchOutputFiles] Attempting to fetch output files from API");
    const response = await fetch('/api/output-files');
    
    if (response.ok) {
      const data = await response.json();
      console.log("[fetchOutputFiles] Successfully fetched output files:", data.files);
      return data.files || [];
    }
    
    console.warn('[fetchOutputFiles] Could not fetch output files from API, status:', response.status);
    return [];
  } catch (e) {
    console.error('[fetchOutputFiles] Error fetching output files:', e);
    return [];
  }
};

// Check image dimensions (if possible)
export const getImageDimensions = async (url: string): Promise<{ width: number, height: number } | null> => {
  // Add logging to debug image loading issues
  console.log('[getImageDimensions] Attempting to load image:', url);
  
  // Check if URL is properly formatted
  if (!url) {
    console.error('[getImageDimensions] Invalid URL provided:', url);
    return null;
  }
  
  // Ensure URL has a leading slash if it's a relative path
  const formattedUrl = url.startsWith('/') || url.startsWith('http') ? url : `/${url}`;
  console.log('[getImageDimensions] Formatted URL:', formattedUrl);
  
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      console.log('[getImageDimensions] Successfully loaded image:', formattedUrl, 'dimensions:', img.naturalWidth, 'x', img.naturalHeight);
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
