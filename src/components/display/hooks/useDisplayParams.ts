
import { useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from '../types';
import { createUrlWithParams, getDefaultParams } from '../utils';

export const useDisplayParams = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const redirectAttemptedRef = useRef(false);
  
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
    showMode: (searchParams.get('show') as ShowMode) || defaultParams.showMode, // Fix: 'show' instead of 'showMode'
    position: (searchParams.get('position') as PositionMode) || defaultParams.position,
    refreshInterval: parseFloatParam(searchParams.get('refresh'), defaultParams.refreshInterval), // Fix: 'refresh' instead of 'refreshInterval'
    backgroundColor: searchParams.get('background') || defaultParams.backgroundColor, // Fix: 'background' instead of 'backgroundColor'
    debugMode: parseBooleanParam(searchParams.get('debug')), // Fix: 'debug' instead of 'debugMode'
    // Properly handling caption and related params
    caption: searchParams.get('caption') || null,
    captionPosition: (searchParams.get('caption-position') as CaptionPosition) || defaultParams.captionPosition, // Fix: hyphenated params
    captionSize: searchParams.get('caption-size') || defaultParams.captionSize,
    captionColor: searchParams.get('caption-color') || defaultParams.captionColor,
    captionFont: searchParams.get('caption-font') || defaultParams.captionFont,
    captionBgColor: searchParams.get('caption-bg-color') ? 
                    `#${searchParams.get('caption-bg-color')}` : 
                    defaultParams.captionBgColor,
    captionBgOpacity: parseFloatParam(searchParams.get('caption-bg-opacity'), defaultParams.captionBgOpacity),
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

  // NEW: Auto-enable debug mode if no parameters are specified
  const hasAnyParams = searchParams.toString().length > 0;
  if (!hasAnyParams) {
    params.debugMode = true;
    console.log('[useDisplayParams] No parameters provided, enabling debug mode automatically');
  }
  
  // Debugging log to show all extracted parameters
  console.log('[useDisplayParams] Parsed params:', params);
  console.log('[useDisplayParams] Caption background:', params.captionBgColor);
  console.log('[useDisplayParams] URL params:', Object.fromEntries(searchParams.entries()));
  console.log('[useDisplayParams] Debug mode enabled:', params.debugMode);
  
  // Helper function to redirect to debug mode if needed
  const redirectToDebugMode = () => {
    // Skip redirection if we already attempted it or if already in debug mode or no output specified
    if (redirectAttemptedRef.current || params.debugMode || !params.output) {
      console.log('[useDisplayParams] Skipping redirect - conditions not met:', {
        alreadyAttempted: redirectAttemptedRef.current,
        alreadyInDebugMode: params.debugMode,
        noOutput: !params.output
      });
      return;
    }
    
    // Mark that we've attempted redirection to prevent loops
    redirectAttemptedRef.current = true;
    console.log('[useDisplayParams] Marked redirection as attempted');
    
    // Only redirect if debugMode parameter is explicitly set to true in URL
    if (searchParams.has('debug') && parseBooleanParam(searchParams.get('debug'))) {
      console.log('[useDisplayParams] Debug mode requested, redirecting');
      const newParams = { ...params, debugMode: true };
      const newUrl = createUrlWithParams(newParams);
      
      console.log('[useDisplayParams] Redirecting to debug mode:', newUrl);
      navigate(newUrl, { replace: true }); // Use replace to avoid building history
    } else {
      console.log('[useDisplayParams] Debug mode not requested in URL');
    }
  };
  
  // Log the current URL for debugging
  useEffect(() => {
    console.log('[useDisplayParams] Current URL:', window.location.href);
  }, []);
  
  return {
    params,
    redirectToDebugMode
  };
};
