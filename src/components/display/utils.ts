// Function to validate and process the output parameter
export const processOutputParam = (outputParam: string | null): string | null => {
  if (!outputParam) return null;
  
  // Check if it's an absolute URL
  if (outputParam.startsWith('http://') || outputParam.startsWith('https://')) {
    return outputParam;
  }
  
  // Otherwise, treat as relative path from /output/
  return `/output/${outputParam}`;
};

// Function to fetch available output files
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // This endpoint would need to be implemented on the server
    const response = await fetch('/api/output-files');
    if (response.ok) {
      return await response.json();
    } else {
      console.error('Failed to fetch output files');
      // Fallback to demo values if endpoint isn't available
      return [
        'sample.jpg',
        'image.png',
        'result.jpg'
      ];
    }
  } catch (err) {
    console.error('Error fetching output files:', err);
    // Use demo values for now
    return [
      'sample.jpg',
      'image.png',
      'result.jpg'
    ];
  }
};

// Format date for display
export const formatDateTime = (date: Date | null) => {
  if (!date) return 'N/A';
  return date.toLocaleTimeString();
};

// Calculate next check time
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number) => {
  if (!lastChecked) return 'N/A';
  const nextCheck = new Date(lastChecked.getTime() + refreshInterval * 1000);
  return formatDateTime(nextCheck);
};
