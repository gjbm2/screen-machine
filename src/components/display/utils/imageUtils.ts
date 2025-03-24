
// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    console.log("[fetchOutputFiles] Attempting to fetch output files from API");
    // First try to fetch from the current server
    const response = await fetch('/api/output-files');
    
    if (response.ok) {
      const data = await response.json();
      console.log("[fetchOutputFiles] Successfully fetched output files:", data.files);
      return data.files || [];
    }
    
    // If that fails, log and return some example files
    console.warn('[fetchOutputFiles] Could not fetch output files from API, status:', response.status);
    return [
      'output/ComfyUI_00001_.png',
      'output/ComfyUI_00002_.png',
      'output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  } catch (e) {
    console.error('[fetchOutputFiles] Error fetching output files:', e);
    // Return example files as a fallback
    return [
      'output/ComfyUI_00001_.png',
      'output/ComfyUI_00002_.png',
      'output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  }
};

// Check image dimensions (if possible)
export const getImageDimensions = async (url: string): Promise<{ width: number, height: number } | null> => {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      resolve({
        width: img.naturalWidth,
        height: img.naturalHeight
      });
    };
    img.onerror = () => {
      console.error('[getImageDimensions] Failed to load image:', url);
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
