
import { useNavigate } from 'react-router-dom';
import { DisplayParams } from '../types';
import { createUrlWithParams, processOutputParam, fullyDecodeUrl } from '../utils/paramUtils';
import { toast } from 'sonner';

interface UseDebugPanelFileManagementProps {
  params: DisplayParams;
}

export const useDebugPanelFileManagement = ({
  params
}: UseDebugPanelFileManagementProps) => {
  const navigate = useNavigate();

  const selectFile = (file: string) => {
    console.log('[useDebugPanelFileManagement] Selected file:', file);
    
    // Handle fully formed URLs (especially with query parameters) differently
    let outputPath;
    if (file.startsWith('http://') || file.startsWith('https://')) {
      // Don't process external URLs - use directly
      console.log('[useDebugPanelFileManagement] Using external URL directly:', file);
      outputPath = file;
    } else {
      // For local files, normalize the path using our utility
      outputPath = processOutputParam(file);
      console.log('[useDebugPanelFileManagement] Normalized local path:', outputPath);
    }
    
    if (!outputPath) {
      console.error('[useDebugPanelFileManagement] Failed to process output path');
      toast.error("Failed to process file path");
      return () => {}; // Return empty function to match expected type
    }
    
    // Create a URL with the debug mode and selected file
    const newParams = {
      ...params,
      output: outputPath,
      debugMode: true
    };
    
    const url = createUrlWithParams(newParams);
    console.log('[useDebugPanelFileManagement] Navigating to:', url);
    
    // Notify the user before navigation
    toast.success(`Loading: ${formatFileName(file)}`);
    
    // Return a function that performs the navigation when called
    return () => {
      console.log('[useDebugPanelFileManagement] Executing navigation function for:', outputPath);
      
      // Navigate to the URL
      navigate(`/display${url}`, { replace: true });
      
      // Force a refresh after navigation to ensure the image loads
      setTimeout(() => {
        console.log('[useDebugPanelFileManagement] Post-navigation check');
        const currentParams = new URLSearchParams(window.location.search);
        const currentOutput = currentParams.get('output');
        
        console.log('[useDebugPanelFileManagement] Current output param:', currentOutput);
        
        // Add additional logging for debugging
        if (currentOutput !== outputPath && currentOutput !== encodeURIComponent(outputPath)) {
          console.warn('[useDebugPanelFileManagement] Output parameter mismatch after navigation');
          console.log('  Expected:', outputPath);
          console.log('  Actual:', currentOutput);
          console.log('  Encoded expected:', encodeURIComponent(outputPath));
          
          // Force page reload as a last resort if the URL isn't what we expect
          window.location.reload();
        }
      }, 300);
    };
  };

  const formatFileName = (file: string) => {
    if (file.startsWith('http')) {
      try {
        const url = new URL(file);
        return url.pathname.split('/').pop() || file;
      } catch (e) {
        return file;
      }
    }
    return file.split('/').pop() || file;
  };

  const isCurrentFile = (file: string, imageUrl: string | null = null) => {
    if (!imageUrl) return false;
    
    // Handle external URLs with query parameters
    let normalizedFile;
    let normalizedImageUrl;
    
    // For external URLs, try to fully decode both before comparison
    if (file.startsWith('http') || file.includes('http%3A')) {
      try {
        normalizedFile = fullyDecodeUrl(file);
      } catch (e) {
        normalizedFile = file;
      }
    } else {
      // Use our path normalization utility for local files
      normalizedFile = processOutputParam(file);
    }
    
    // Same for image URL
    if (imageUrl.startsWith('http') || imageUrl.includes('http%3A')) {
      try {
        normalizedImageUrl = fullyDecodeUrl(imageUrl);
      } catch (e) {
        normalizedImageUrl = imageUrl;
      }
    } else {
      normalizedImageUrl = processOutputParam(imageUrl);
    }
    
    // Compare the base URLs without query parameters for more robust matching
    try {
      // Handle non-URL strings by checking direct equality
      if (!normalizedFile.startsWith('http') || !normalizedImageUrl.startsWith('http')) {
        console.log('[useDebugPanelFileManagement] Using direct string comparison:', {
          normalizedFile,
          normalizedImageUrl,
          isMatch: normalizedImageUrl === normalizedFile
        });
        return normalizedImageUrl === normalizedFile;
      }
      
      const fileUrlObj = new URL(normalizedFile);
      const imageUrlObj = new URL(normalizedImageUrl);
      
      // Compare pathnames for more accurate matching
      const isPathMatch = fileUrlObj.pathname === imageUrlObj.pathname;
      
      console.log('[useDebugPanelFileManagement] Comparing URLs:', {
        filePathname: fileUrlObj.pathname,
        imagePathname: imageUrlObj.pathname,
        isPathMatch
      });
      
      return isPathMatch;
    } catch (e) {
      // If URL parsing fails, fall back to simple string comparison
      console.log('[useDebugPanelFileManagement] URL parsing failed, using string comparison:', {
        normalizedFile,
        normalizedImageUrl,
        isMatch: normalizedImageUrl === normalizedFile
      });
      
      return normalizedImageUrl === normalizedFile;
    }
  };

  const formatTime = (timeValue: Date | string | null) => {
    if (!timeValue) return 'Never';
    
    try {
      const date = timeValue instanceof Date ? timeValue : new Date(timeValue);
      return date.toLocaleTimeString();
    } catch (e) {
      return String(timeValue);
    }
  };

  return {
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  };
};
