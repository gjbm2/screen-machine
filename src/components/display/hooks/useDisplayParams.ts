
import { useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from '../types';
import { createUrlWithParams, getDefaultParams } from '../utils';

export const useDisplayParams = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  
  const parseBooleanParam = (value: string | null): boolean => 
    value === 'true' || value === '1';
  
  const parseFloatParam = (value: string | null, defaultValue: number): number => {
    if (!value) return defaultValue;
    const parsed = parseFloat(value);
    return isNaN(parsed) ? defaultValue : parsed;
  };
  
  const defaultParams = getDefaultParams();
  
  // Parse and extract all URL parameters
  const params: DisplayParams = {
    output: searchParams.get('output') || null,
    showMode: (searchParams.get('showMode') as ShowMode) || defaultParams.showMode,
    position: (searchParams.get('position') as PositionMode) || defaultParams.position,
    refreshInterval: parseFloatParam(searchParams.get('refreshInterval'), defaultParams.refreshInterval),
    backgroundColor: searchParams.get('backgroundColor') || defaultParams.backgroundColor,
    debugMode: parseBooleanParam(searchParams.get('debugMode')),
    // Properly handling caption and related params
    caption: searchParams.get('caption') || null,
    captionPosition: (searchParams.get('captionPosition') as CaptionPosition) || defaultParams.captionPosition,
    captionSize: searchParams.get('captionSize') || defaultParams.captionSize,
    captionColor: searchParams.get('captionColor') || defaultParams.captionColor,
    captionFont: searchParams.get('captionFont') || defaultParams.captionFont,
    captionBgColor: searchParams.get('captionBgColor') || defaultParams.captionBgColor,
    captionBgOpacity: parseFloatParam(searchParams.get('captionBgOpacity'), defaultParams.captionBgOpacity),
    transition: (searchParams.get('transition') as TransitionType) || defaultParams.transition,
  };
  
  // Add data parameter if it exists
  if (searchParams.has('data')) {
    try {
      // Attempt to parse data as JSON
      params.data = JSON.parse(searchParams.get('data') || '{}');
    } catch (e) {
      console.error('Failed to parse data parameter:', e);
      params.data = { error: 'Failed to parse data' };
    }
  }
  
  // Debugging log to show all extracted parameters
  console.log('[useDisplayParams] Parsed params:', params);
  console.log('[useDisplayParams] Caption background:', params.captionBgColor);
  
  // Helper function to redirect to debug mode if needed
  const redirectToDebugMode = () => {
    // Skip if already in debug mode or no output is specified
    if (params.debugMode || !params.output) return;
    
    // If both output and debugMode are specified, force debug mode
    if (searchParams.has('output') && searchParams.has('debugMode')) {
      // If debugMode is explicitly set to 'false', don't override
      if (!parseBooleanParam(searchParams.get('debugMode'))) return;
      
      const newParams = { ...params, debugMode: true };
      const newUrl = createUrlWithParams(newParams);
      
      console.log('[useDisplayParams] Redirecting to debug mode');
      navigate(newUrl);
    }
  };
  
  // Log the current URL for debugging
  useEffect(() => {
    console.log('[useDisplayParams] Current URL:', window.location.href);
    console.log('[useDisplayParams] URL params:', Object.fromEntries(searchParams.entries()));
  }, [searchParams]);
  
  return {
    params,
    redirectToDebugMode
  };
};
