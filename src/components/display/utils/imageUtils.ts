
// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    console.log("[fetchOutputFiles] Attempting to fetch output files from API");
    // First try to fetch from the current server
    const response = await fetch('/api/output-files');
    
    if (response.ok) {
      const data = await response.json();
      console.log("[fetchOutputFiles] Successfully fetched output files:", data.files);
      
      // Add leading slash to paths for proper URL formation
      const formattedFiles = (data.files || []).map((file: string) => {
        return file.startsWith('/') ? file : `/${file}`;
      });
      
      return formattedFiles;
    }
    
    // If that fails, log and return some example files with correct path format
    console.warn('[fetchOutputFiles] Could not fetch output files from API, status:', response.status);
    return [
      '/output/ComfyUI_00001_.png',
      '/output/ComfyUI_00002_.png',
      '/output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  } catch (e) {
    console.error('[fetchOutputFiles] Error fetching output files:', e);
    // Return example files as a fallback with correct path format
    return [
      '/output/ComfyUI_00001_.png',
      '/output/ComfyUI_00002_.png',
      '/output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  }
};

// Check image dimensions (if possible)
export const getImageDimensions = async (url: string): Promise<{ width: number, height: number } | null> => {
  // Add logging to debug image loading issues
  console.log('[getImageDimensions] Attempting to load image:', url);
  
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      console.log('[getImageDimensions] Successfully loaded image:', url, 'dimensions:', img.naturalWidth, 'x', img.naturalHeight);
      resolve({
        width: img.naturalWidth,
        height: img.naturalHeight
      });
    };
    img.onerror = (error) => {
      console.error('[getImageDimensions] Failed to load image:', url, 'Error:', error);
      resolve(null);
    };
    img.src = url;
  });
};

// Calculate next check time
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number): Date | null => {
  if (!lastChecked) return null;
  return new Date(lastChecked.getTime() + refreshInterval * 1000);
};
