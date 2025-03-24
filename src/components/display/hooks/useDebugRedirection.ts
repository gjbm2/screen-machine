
import { useRef } from 'react';
import { DisplayParams } from '../types';

export const useDebugRedirection = (
  params: DisplayParams,
  redirectToDebugMode: () => void
) => {
  const redirectAttemptedRef = useRef(false);
  const debugHandledRef = useRef(false);

  const checkDebugRedirection = () => {
    console.log('[useDebugRedirection] Checking if debug redirection is needed:', {
      params,
      alreadyAttempted: redirectAttemptedRef.current,
      hasOutput: !!params.output,
      debugMode: params.debugMode,
      debugHandled: debugHandledRef.current
    });
    
    // We should enter debug mode in two scenarios:
    // 1. When there's an output param but we're not in debug mode
    // 2. When there's no output param at all (blank display page)
    if (!redirectAttemptedRef.current && !debugHandledRef.current && !params.debugMode) {
      redirectAttemptedRef.current = true;
      console.log('[useDebugRedirection] Attempting debug redirection check');
      
      // First visit to display page or has output param but not in debug mode
      try {
        redirectToDebugMode();
        console.log('[useDebugRedirection] Redirecting to debug mode');
      } catch (err) {
        console.error('[useDebugRedirection] Error during debug redirection:', err);
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
    checkDebugRedirection
  };
};
