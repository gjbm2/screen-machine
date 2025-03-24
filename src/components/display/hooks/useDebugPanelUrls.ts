
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
    // Actually apply settings by updating the URL
    console.log('[useDebugPanelUrls] Applying settings...');
    
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
      debugMode: true // Maintain debug mode
    };
    
    // Generate URL for debug mode
    const url = createUrlWithParams(newParams);
    console.log('[useDebugPanelUrls] Applying settings, navigating to:', url);
    
    // Use navigate instead of direct location change to avoid full reload
    navigate(`/display${url}`);
    
    toast({
      title: "Settings Applied",
      description: "Display settings have been updated.",
    });
  };
  
  const resetDisplay = () => {
    // Reset to base display URL with no parameters
    window.location.href = '/display';
    toast({
      title: "Settings Reset",
      description: "All settings have been reset to default values.",
    });
  };
  
  const commitSettings = () => {
    // Use the actual current image URL from params instead of customUrl when available
    const outputToUse = params.output || customUrl;
    console.log('[useDebugPanelUrls] Using output for view mode:', outputToUse);
    
    // Validate that we have an output URL
    if (!outputToUse) {
      console.error('[useDebugPanelUrls] No output URL for view mode, cannot commit');
      toast({
        title: "Error",
        description: "No image URL specified. Please select an image file or enter a URL.",
        variant: "destructive"
      });
      return;
    }
    
    // Process the output to ensure it's properly formatted
    const processedOutput = processOutputParam(outputToUse);
    console.log('[useDebugPanelUrls] Processed output for view mode:', processedOutput);
    
    // Clear any stale local storage flags that might affect navigation
    try {
      // Store a flag in localStorage to indicate this was an explicit exit from debug mode
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
      debugMode: false // Explicitly set debugMode to false
    };
    
    // Generate URL from params
    const url = createUrlWithParams(newParams);
    console.log('[useDebugPanelUrls] Committing settings, navigating to view mode:', url);
    
    // Use direct window.location change for view mode to ensure a clean state
    // This forces a complete page reload which should clear any React state
    window.location.href = `/display${url}`;
    
    toast({
      title: "View Mode Activated",
      description: "Settings applied and debug mode disabled.",
    });
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
    
    toast({
      title: "URL Copied",
      description: "Display URL has been copied to clipboard.",
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
