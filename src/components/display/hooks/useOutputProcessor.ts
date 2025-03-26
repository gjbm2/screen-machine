
import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam, fullyDecodeUrl } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[DEBUG useOutputProcessor] Raw output param:', params.output);
      
      // For deeply nested external URLs, make sure they're fully decoded
      if (params.output.includes('%') && (
          params.output.includes('http%3A') || 
          params.output.includes('https%3A')
        )) {
        try {
          console.log('[DEBUG useOutputProcessor] Attempting to decode complex URL');
          const decodedUrl = fullyDecodeUrl(params.output);
          console.log('[DEBUG useOutputProcessor] Fully decoded external URL:', decodedUrl);
          
          // Check if the resulting URL seems valid
          if (decodedUrl.startsWith('http')) {
            console.log('[DEBUG useOutputProcessor] Decoded URL appears valid');
            
            // Test if the URL might be reachable
            const testImg = new Image();
            testImg.onload = () => console.log('[DEBUG useOutputProcessor] Test image loaded from URL');
            testImg.onerror = (e) => console.error('[DEBUG useOutputProcessor] Test image failed to load:', e);
            testImg.src = decodedUrl;
            
            // Modify the param directly
            console.log('[DEBUG useOutputProcessor] Setting output param to decoded URL');
            params.output = decodedUrl;
            return;
          } else {
            console.warn('[DEBUG useOutputProcessor] Decoded URL does not start with http');
          }
        } catch (e) {
          console.error('[DEBUG useOutputProcessor] Failed to decode complex URL:', e);
        }
      }
      
      // Skip processing for external URLs
      if (params.output.startsWith('http://') || params.output.startsWith('https://')) {
        console.log('[DEBUG useOutputProcessor] External URL, using as-is:', params.output);
        
        // Test if URL might be reachable
        const testImg = new Image();
        testImg.onload = () => console.log('[DEBUG useOutputProcessor] Test image loaded from URL');
        testImg.onerror = (e) => console.error('[DEBUG useOutputProcessor] Test image failed to load:', e);
        testImg.src = params.output;
        
        return;
      }
      
      // For local paths, normalize the format
      const processedOutput = processOutputParam(params.output);
      
      if (processedOutput !== params.output) {
        console.log('[DEBUG useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
        params.output = processedOutput;
      }
    }
  }, [params.output]);
};
