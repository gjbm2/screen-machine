import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[useOutputProcessor] Raw output param before processing:', params.output);
      
      try {
        // Check if it's a URL with query parameters
        if (params.output.includes('?') && (params.output.startsWith('http://') || params.output.startsWith('https://'))) {
          console.log('[useOutputProcessor] Complex URL with query params detected, preserving as-is');
          // For complex URLs with query params, we keep them as-is without additional processing
          // that might break the query parameters
        } else {
          // For simpler URLs or paths, proceed with normal processing
          const processedOutput = processOutputParam(params.output);
          
          if (processedOutput !== params.output) {
            console.log('[useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
            params.output = processedOutput;
          } else {
            console.log('[useOutputProcessor] Output param already in correct format:', params.output);
          }
        }
      } catch (error) {
        console.error('[useOutputProcessor] Error processing URL:', error);
        // In case of error, keep the original URL
      }
    }
  }, [params.output]);
};
