
import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam, fullyDecodeUrl } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[useOutputProcessor] Raw output param:', params.output);
      
      // For deeply nested external URLs, make sure they're fully decoded
      if (params.output.includes('%') && (
          params.output.includes('http%3A') || 
          params.output.includes('https%3A')
        )) {
        try {
          const decodedUrl = fullyDecodeUrl(params.output);
          console.log('[useOutputProcessor] Fully decoded external URL:', decodedUrl);
          params.output = decodedUrl;
          return;
        } catch (e) {
          console.error('[useOutputProcessor] Failed to decode complex URL:', e);
        }
      }
      
      // Skip processing for external URLs
      if (params.output.startsWith('http://') || params.output.startsWith('https://')) {
        console.log('[useOutputProcessor] External URL, using as-is:', params.output);
        return;
      }
      
      // For local paths, normalize the format
      const processedOutput = processOutputParam(params.output);
      
      if (processedOutput !== params.output) {
        console.log('[useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
        params.output = processedOutput;
      }
    }
  }, [params.output]);
};
