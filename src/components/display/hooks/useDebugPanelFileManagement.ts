
import { useNavigate } from 'react-router-dom';
import { DisplayParams } from '../types';
import { createUrlWithParams } from '../utils/paramUtils';

interface UseDebugPanelFileManagementProps {
  params: DisplayParams;
}

export const useDebugPanelFileManagement = ({
  params
}: UseDebugPanelFileManagementProps) => {
  const navigate = useNavigate();

  const selectFile = (file: string) => {
    return () => {
      navigate(createUrlWithParams({
        ...params,
        output: encodeURIComponent(file)
      }));
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
    return file;
  };

  const isCurrentFile = (file: string, imageUrl: string | null) => {
    if (!imageUrl) return false;
    
    if (imageUrl.startsWith('http')) {
      return imageUrl === file;
    } else {
      const currentFile = imageUrl.split('/').pop();
      const compareFile = file.split('/').pop();
      return currentFile === compareFile;
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
