
// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // First try to fetch from the current server
    const response = await fetch('/api/output-files');
    if (response.ok) {
      const data = await response.json();
      return data.files || [];
    }
    
    // If that fails, return some example files
    console.warn('Could not fetch output files from API');
    return [
      'output/ComfyUI_00001_.png',
      'output/ComfyUI_00002_.png',
      'output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  } catch (e) {
    console.error('Error fetching output files:', e);
    return [];
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
