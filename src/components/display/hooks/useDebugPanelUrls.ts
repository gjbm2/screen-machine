import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { DisplayParams } from '../types';
import { createUrlWithParams } from '../utils/paramUtils';

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

  const processCustomUrl = (url: string) => {
    if (!url) return null;
    
    if (url.startsWith('http')) {
      return url;
    }
    
    if (url.startsWith('/')) return url;
    if (url.startsWith('output/')) return `/${url}`;
    
    return `/output/${url}`;
  };

  const generateUrl = (includeDebug = false) => {
    const processedOutput = customUrl ? processCustomUrl(customUrl) : null;
    
    const newParams: DisplayParams = {
      output: processedOutput,
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
        acc[key] = value;
      }
      return acc;
    }, {} as Partial<DisplayParams>);
    
    return createUrlWithParams(cleanParams as DisplayParams);
  };

  const applySettings = () => {
    const processedOutput = customUrl ? processCustomUrl(customUrl) : null;
    
    const newParams: DisplayParams = {
      output: processedOutput,
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
    console.log('[useDebugPanelUrls] Applying settings, navigating to:', url);
    navigate(url);
    toast.success("Settings applied");
  };

  const resetDisplay = () => {
    navigate('/display');
    toast.success("Display reset to defaults");
  };

  const commitSettings = () => {
    const url = generateUrl(false);
    console.log('[useDebugPanelUrls] Committing settings, navigating to:', url);
    
    setTimeout(() => {
      navigate(url);
      toast.success("Settings committed");
    }, 0);
  };

  const copyUrl = () => {
    const relativeUrl = generateUrl(false);
    const currentPath = window.location.pathname;
    const baseUrl = window.location.origin;
    
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
