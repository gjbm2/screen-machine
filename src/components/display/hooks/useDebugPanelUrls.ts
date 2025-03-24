
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { DisplayParams } from '../types';
import { createUrlWithParams } from '../utils';

interface UseDebugPanelUrlsProps {
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

export const useDebugPanelUrls = ({
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
}: UseDebugPanelUrlsProps) => {
  const navigate = useNavigate();

  const generateUrl = (includeDebug = false) => {
    const encodedOutput = customUrl ? encodeURIComponent(customUrl) : null;
    
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
    
    // Filter out default values to make URL cleaner
    const cleanParams = Object.entries(newParams).reduce((acc, [key, value]) => {
      if (key === 'debugMode' && includeDebug) {
        acc[key] = value;
      } else if (key === 'output') {
        if (value !== null) acc[key] = value;
      } else if (key !== 'debugMode' && value !== null && value !== undefined) {
        // Only include non-default parameters
        acc[key] = value;
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
    // Create a URL without debug mode
    const url = generateUrl(false);
    
    // We need to prevent navigation from being immediately overridden
    // by using setTimeout to break out of the current execution context
    setTimeout(() => {
      navigate(url);
      toast.success("Settings committed");
    }, 0);
  };

  const copyUrl = () => {
    // Get the full URL including the domain name and path, without debug mode
    const relativeUrl = generateUrl(false);
    const currentPath = window.location.pathname;
    const baseUrl = window.location.origin;
    
    // Ensure we have the correct base path (either '/display' or the current path)
    const basePath = currentPath.includes('/display') ? '/display' : currentPath;
    const fullUrl = `${baseUrl}${basePath}${relativeUrl}`;
    
    console.log('[useDebugPanelUrls] Copying URL:', fullUrl);
    
    navigator.clipboard.writeText(fullUrl)
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

  return {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl
  };
};
