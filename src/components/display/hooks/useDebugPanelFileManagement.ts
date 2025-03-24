
import { useNavigate } from 'react-router-dom';
import { DisplayParams } from '../types';
import { createUrlWithParams } from '../utils/paramUtils';
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
      
      // Notify the user
      toast({
        title: "Image Selected",
        description: `Now displaying: ${file.split('/').pop() || file}`,
      });
      
      // Normalize the path
      let outputPath = file;
      if (!outputPath.startsWith('/') && !outputPath.startsWith('http')) {
        outputPath = `/output/${outputPath}`;
      }
      
      // Create a URL with the debug mode and selected file
      const newParams = {
        ...params,
        output: outputPath,
        debugMode: true
      };
      
      const url = createUrlWithParams(newParams);
      console.log('[useDebugPanelFileManagement] Navigating to:', url);
      
      // Navigate to the URL
      navigate(url);
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
    
    // Normalize paths for comparison
    const normalizedFile = file.startsWith('/') ? file : `/output/${file}`;
    const normalizedImageUrl = imageUrl.includes('/output/') ? 
      imageUrl : 
      (imageUrl.startsWith('/') ? imageUrl : `/output/${imageUrl}`);
    
    console.log('[useDebugPanelFileManagement] Comparing:', {
      normalizedFile,
      normalizedImageUrl,
      isMatch: normalizedImageUrl.includes(normalizedFile)
    });
    
    return normalizedImageUrl.includes(normalizedFile);
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
