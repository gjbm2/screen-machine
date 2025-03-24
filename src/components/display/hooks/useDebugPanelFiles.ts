
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { DisplayParams } from '../types';
import { createUrlWithParams } from '../utils';

interface UseDebugPanelFilesProps {
  params: DisplayParams;
  customUrl: string;
  showMode: DisplayParams['showMode'];
  position: DisplayParams['position'];
  refreshInterval: number;
  backgroundColor: string;
  caption: string | null;
  captionPosition: DisplayParams['captionPosition'];
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  transition: DisplayParams['transition'];
  setCopied: (value: boolean) => void;
}

export const useDebugPanelFiles = ({
  params,
  customUrl,
  showMode,
  position,
  refreshInterval,
  backgroundColor,
  caption,
  captionPosition,
  captionSize,
  captionColor,
  captionFont,
  captionBgColor,
  captionBgOpacity,
  transition,
  setCopied
}: UseDebugPanelFilesProps) => {
  const navigate = useNavigate();

  const generateUrl = (includeDebug = false) => {
    const encodedOutput = customUrl ? encodeURIComponent(customUrl) : null;
    const defaultParams = createUrlWithParams({} as DisplayParams);
    
    const newParams: DisplayParams = {
      output: encodedOutput,
      showMode,
      position,
      refreshInterval,
      backgroundColor,
      debugMode: includeDebug,
      caption: caption || null,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      data: params.data,
      transition,
    };
    
    const cleanParams = Object.entries(newParams).reduce((acc, [key, value]) => {
      if (key === 'debugMode' && includeDebug) {
        acc[key] = value;
      } else if (key === 'output') {
        if (value !== null) acc[key] = value;
      } else if (key !== 'debugMode' && value !== null && value !== undefined) {
        // Only include if different from default
        const defaultParamValue = (defaultParams as any)[key];
        if (String(value) !== String(defaultParamValue)) {
          acc[key] = value;
        }
      }
      return acc;
    }, {} as Partial<DisplayParams>);
    
    return createUrlWithParams(cleanParams as DisplayParams);
  };

  const applySettings = () => {
    const encodedOutput = customUrl ? encodeURIComponent(customUrl) : null;
    
    const newParams: DisplayParams = {
      output: encodedOutput,
      showMode,
      position,
      refreshInterval,
      backgroundColor,
      debugMode: true,
      caption: caption || null,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      data: params.data,
      transition,
    };
    
    const url = createUrlWithParams(newParams);
    navigate(url);
    toast.success("Settings applied");
  };

  const resetDisplay = () => {
    navigate('/display');
    toast.success("Display reset to defaults");
  };

  const commitSettings = () => {
    // Create URL without debug mode flag
    const url = generateUrl(false);
    
    // Force navigation to non-debug mode URL
    window.location.href = url;
    toast.success("Settings committed");
  };

  const copyUrl = () => {
    const url = window.location.origin + generateUrl(false);
    navigator.clipboard.writeText(url)
      .then(() => {
        setCopied(true);
        toast.success("URL copied to clipboard");
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(err => {
        console.error('Failed to copy URL: ', err);
        toast.error("Failed to copy URL");
      });
  };

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
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  };
};
