
import { useRef, useEffect } from 'react';
import { DisplayParams } from '../types';

export const useDebugRedirection = (
  params: DisplayParams,
  redirectToDebugMode: () => void
) => {
  const redirectAttemptedRef = useRef(false);
  const debugHandledRef = useRef(false);
  const userExplicitlyExitedDebugRef = useRef(false);

  // This effect detects when the user has explicitly exited debug mode
  useEffect(() => {
    // If debug mode changes from true to false AND we previously had it as true
    // then it's likely the user explicitly turned it off (via Commit button)
    if (debugHandledRef.current && !params.debugMode) {
      console.log('[useDebugRedirection] User explicitly exited debug mode');
      userExplicitlyExitedDebugRef.current = true;
      
      // Store a flag in localStorage to indicate this was an explicit exit from debug mode
      try {
        localStorage.setItem('userExplicitlyExitedDebug', 'true');
        console.log('[useDebugRedirection] Set localStorage flag for explicit debug exit');
      } catch (e) {
        console.error('[useDebugRedirection] Error setting localStorage flag:', e);
      }
    }
  }, [params.debugMode]);

  const checkDebugRedirection = () => {
    console.log('[useDebugRedirection] Checking if debug redirection is needed:', {
      params,
      alreadyAttempted: redirectAttemptedRef.current,
      hasOutput: !!params.output,
      debugMode: params.debugMode,
      debugHandled: debugHandledRef.current,
      userExplicitlyExited: userExplicitlyExitedDebugRef.current
    });
    
    // Skip redirection if the user has explicitly exited debug mode
    if (userExplicitlyExitedDebugRef.current) {
      console.log('[useDebugRedirection] Skipping redirection - user explicitly exited debug mode');
      return;
    }
    
    // We should enter debug mode in two scenarios:
    // 1. When there's an output param but we're not in debug mode
    // 2. When there's no output param at all (blank display page)
    if (!redirectAttemptedRef.current && !debugHandledRef.current && !params.debugMode) {
      redirectAttemptedRef.current = true;
      console.log('[useDebugRedirection] Attempting debug redirection check');
      
      if (!params.output) {
        // First visit to display page with no output - go to debug mode
        try {
          redirectToDebugMode();
          console.log('[useDebugRedirection] Redirecting to debug mode - no output param');
        } catch (err) {
          console.error('[useDebugRedirection] Error during debug redirection:', err);
        }
      }
    } else if (params.debugMode && !debugHandledRef.current) {
      // Mark as handled if already in debug mode
      debugHandledRef.current = true;
      console.log('[useDebugRedirection] Already in debug mode, marking as handled');
    }
  };

  return {
    redirectAttemptedRef,
    debugHandledRef,
    userExplicitlyExitedDebugRef,
    checkDebugRedirection
  };
};
