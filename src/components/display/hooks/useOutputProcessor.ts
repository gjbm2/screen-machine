
import { useEffect, useState } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam, fullyDecodeUrl } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  const [processedUrl, setProcessedUrl] = useState<string | null>(null);
  
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (!params.output) {
      console.log('[useOutputProcessor] No output param to process');
      setProcessedUrl(null);
      return;
    }
    
    console.log('[useOutputProcessor] Raw output param:', params.output);
    
    // Skip processing for URLs that are already properly formatted
    if (params.output.startsWith('http://') || params.output.startsWith('https://')) {
      console.log('[useOutputProcessor] Already a valid URL, keeping as is:', params.output);
      setProcessedUrl(params.output);
      
      // Verify URL is accessible
      const testImg = new Image();
      testImg.onload = () => console.log('[useOutputProcessor] Test image loaded successfully from external URL:', params.output);
      testImg.onerror = (e) => console.error('[useOutputProcessor] Test image failed to load from external URL:', e);
      testImg.src = params.output;
      return;
    }
    
    // For deeply nested external URLs, make sure they're fully decoded
    if (params.output.includes('%') && (
        params.output.includes('http%3A') || 
        params.output.includes('https%3A')
      )) {
      try {
        console.log('[useOutputProcessor] Attempting to decode complex URL');
        const decodedUrl = fullyDecodeUrl(params.output);
        console.log('[useOutputProcessor] Fully decoded external URL:', decodedUrl);
        
        // Check if the resulting URL seems valid
        if (decodedUrl.startsWith('http')) {
          console.log('[useOutputProcessor] Decoded URL appears valid');
          setProcessedUrl(decodedUrl);
          
          // Verify URL is accessible
          const testImg = new Image();
          testImg.onload = () => console.log('[useOutputProcessor] Test image loaded successfully from decoded URL:', decodedUrl);
          testImg.onerror = (e) => console.error('[useOutputProcessor] Test image failed to load from decoded URL:', e);
          testImg.src = decodedUrl;
          return;
        } else {
          console.warn('[useOutputProcessor] Decoded URL does not start with http');
        }
      } catch (e) {
        console.error('[useOutputProcessor] Failed to decode complex URL:', e);
      }
    }
    
    // For local paths, normalize the format
    const processedOutput = processOutputParam(params.output);
    console.log('[useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
    
    // Verify path is accessible
    if (processedOutput) {
      const testImg = new Image();
      testImg.onload = () => console.log('[useOutputProcessor] Test image loaded successfully from local path:', processedOutput);
      testImg.onerror = (e) => console.error('[useOutputProcessor] Test image failed to load from local path:', processedOutput, e);
      testImg.src = processedOutput;
    }
    
    setProcessedUrl(processedOutput);
  }, [params.output]);
  
  return { processedUrl };
};
