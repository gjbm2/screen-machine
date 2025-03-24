
import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[useOutputProcessor] Processing output param:', params.output);
      const processedOutput = processOutputParam(params.output);
      
      if (processedOutput !== params.output) {
        console.log('[useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
        // Since params is likely immutable, we just log the change
        // The actual use of processedOutput should be in other hooks that consume this
      }
    }
  }, [params.output]);
  
  return {
    processedOutput: params.output ? processOutputParam(params.output) : null
  };
};
