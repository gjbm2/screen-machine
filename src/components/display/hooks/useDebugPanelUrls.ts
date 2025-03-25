import { useNavigate } from 'react-router-dom';
import { DisplayParams } from '../types';
import { createUrlWithParams, processOutputParam } from '../utils/paramUtils';
import { toast } from 'sonner';

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

  // Generate a URL with the current parameters
  const generateUrl = () => {
    // Process the customUrl to ensure it's properly formatted
    const processedOutput = processOutputParam(customUrl);
    console.log('[useDebugPanelUrls] Processed custom URL:', processedOutput);
    
    // Create new params object with all current settings
    const newParams: DisplayParams = {
      ...params,
      output: processedOutput,
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
    };
    
    // Generate URL from params
    const urlParams = createUrlWithParams(newParams);
    console.log('[useDebugPanelUrls] Generated URL parameters:', urlParams);
    
    return `/display${urlParams}`;
  };
  
  const applySettings = () => {
    // Use the actual current image URL from params instead of customUrl when available
    const outputToUse = params.output || customUrl;
    console.log('[useDebugPanelUrls] Using output for applying settings:', outputToUse);
    
    // Process the output to ensure it's properly formatted
    const processedOutput = processOutputParam(outputToUse);
    console.log('[useDebugPanelUrls] Processed output for applying settings:', processedOutput);
    
    // Create new params object with all current settings, keeping debug mode
    const newParams: DisplayParams = {
      ...params,
      output: processedOutput,
      showMode,
      position,
      refreshInterval: 0,
      backgroundColor,
      caption,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      transition,
      debugMode: true
    };
    
    // Generate URL for debug mode
    const url = createUrlWithParams(newParams);
    console.log('[useDebugPanelUrls] Applying settings, navigating to:', url);
    
    // Use navigate instead of direct location change to avoid full reload
    navigate(`/display${url}`);
    
    toast("Settings Applied", {
      description: "Display settings have been updated."
    });
  };
  
  const resetDisplay = () => {
    // Reset to base display URL with no parameters
    window.location.href = '/display';
    toast("Settings Reset", {
      description: "All settings have been reset to default values."
    });
  };
  
  const commitSettings = (): string | null => {
    // Use the actual current image URL from params instead of customUrl when available
    const outputToUse = params.output || customUrl;
    console.log('[useDebugPanelUrls] Using output for view mode:', outputToUse);
    
    // Validate that we have an output URL
    if (!outputToUse) {
      console.error('[useDebugPanelUrls] No output URL for view mode, cannot commit');
      toast.error("No image URL specified. Please select an image file or enter a URL.");
      return null;
    }
    
    // Process the output to ensure it's properly formatted
    const processedOutput = processOutputParam(outputToUse);
    console.log('[useDebugPanelUrls] Processed output for view mode:', processedOutput);
    
    // Store a flag in localStorage to indicate this was an explicit exit from debug mode
    // This is critical for preventing the automatic redirection back to debug mode
    try {
      localStorage.setItem('userExplicitlyExitedDebug', 'true');
      console.log('[useDebugPanelUrls] Set localStorage flag for explicit debug exit');
    } catch (e) {
      console.error('[useDebugPanelUrls] Error setting localStorage flag:', e);
    }
    
    // Create new params object with all current settings, but WITHOUT debug mode
    const newParams: DisplayParams = {
      ...params,
      output: processedOutput,
      showMode,
      position,
      refreshInterval: 0,
      backgroundColor,
      caption,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      captionBgColor,
      captionBgOpacity,
      transition,
      debugMode: false
    };
    
    // Generate URL from params
    const url = createUrlWithParams(newParams);
    console.log('[useDebugPanelUrls] Committing settings, navigating to view mode:', url);
    
    // Return the full URL
    const fullUrl = `/display${url}`;
    console.log('[useDebugPanelUrls] Navigation URL:', fullUrl);
    
    // Set the flag first
    localStorage.setItem('debugModeExitTime', Date.now().toString());
    
    return fullUrl;
  };
  
  const copyUrl = () => {
    const url = generateUrl();
    
    // Get the full URL including domain
    const fullUrl = window.location.origin + url;
    console.log('[useDebugPanelUrls] Copying URL to clipboard:', fullUrl);
    
    // Copy the URL to clipboard
    navigator.clipboard.writeText(fullUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    
    toast("URL Copied", {
      description: "Display URL has been copied to clipboard."
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
