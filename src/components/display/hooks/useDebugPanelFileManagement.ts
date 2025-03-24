
import { useNavigate } from 'react-router-dom';
import { DisplayParams } from '../types';
import { createUrlWithParams, processOutputParam, normalizePathForDisplay } from '../utils/paramUtils';
import { toast } from '@/hooks/use-toast';

interface UseDebugPanelFileManagementProps {
  params: DisplayParams;
}

export const useDebugPanelFileManagement = ({
  params
}: UseDebugPanelFileManagementProps) => {
  const navigate = useNavigate();

  const selectFile = (file: string) => {
    return () => {
      console.log('[useDebugPanelFileManagement] Selected file:', file);
      
      // Normalize the path first using our shared utility
      const outputPath = processOutputParam(file);
      console.log('[useDebugPanelFileManagement] Normalized path with processOutputParam:', outputPath);
      
      if (!outputPath) {
        console.error('[useDebugPanelFileManagement] Failed to process output path');
        toast.error("Failed to process file path");
        return;
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
      toast({
        title: "Image Selected",
        description: `Loading: ${formatFileName(file)}`,
      });
      
      // Navigate to the URL
      navigate(`/display${url}`, { replace: false });
      
      // Force a refresh after navigation to ensure the image loads
      setTimeout(() => {
        console.log('[useDebugPanelFileManagement] Post-navigation check');
        const currentParams = new URLSearchParams(window.location.search);
        const currentOutput = currentParams.get('output');
        
        console.log('[useDebugPanelFileManagement] Current output param:', currentOutput);
        
        // If we have the right param but image isn't showing, consider forcing a refresh
        if (currentOutput === outputPath) {
          console.log('[useDebugPanelFileManagement] Output param is set correctly');
        }
      }, 500);
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

  const isCurrentFile = (file: string, imageUrl: string | null) => {
    if (!imageUrl) return false;
    
    // Use our path normalization utility for consistent comparison
    const normalizedFile = processOutputParam(file);
    const normalizedImageUrl = processOutputParam(imageUrl);
    
    console.log('[useDebugPanelFileManagement] Comparing:', {
      normalizedFile,
      normalizedImageUrl,
      isMatch: normalizedImageUrl === normalizedFile
    });
    
    // More exact comparison now that we've normalized both paths
    return normalizedImageUrl === normalizedFile;
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
