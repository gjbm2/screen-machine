
import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam, decodeComplexOutputParam } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[useOutputProcessor] Raw output param before processing:', params.output);
      
      try {
        // Check if it's a URL with query parameters
        if (params.output.includes('?') && (params.output.startsWith('http://') || params.output.startsWith('https://'))) {
          console.log('[useOutputProcessor] Complex URL with query params detected, ensuring proper decoding');
          
          // For complex URLs, ensure it's fully decoded
          // We don't modify params.output here as it may already be handled by decodeComplexOutputParam
          // in useDisplayParams, but we log for debugging
          const fullyDecoded = decodeComplexOutputParam(params.output);
          console.log('[useOutputProcessor] Fully decoded URL:', fullyDecoded);
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
