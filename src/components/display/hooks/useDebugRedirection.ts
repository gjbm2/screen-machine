
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
    
    if (!redirectAttemptedRef.current && params.output && !debugHandledRef.current) {
      redirectAttemptedRef.current = true;
      console.log('[useDebugRedirection] Attempting debug redirection check');
      
      // If we're in debug mode, mark it as handled to prevent further checks
      if (params.debugMode) {
        debugHandledRef.current = true;
        console.log('[useDebugRedirection] Already in debug mode, marking as handled');
      } else {
        // Only redirect if not already in debug mode
        try {
          redirectToDebugMode();
        } catch (err) {
          console.error('[useDebugRedirection] Error during debug redirection:', err);
        }
      }
    }
  };

  return {
    redirectAttemptedRef,
    debugHandledRef,
    checkDebugRedirection
  };
};
