
import { useEffect } from 'react';
import { DisplayParams } from '../types';
import { processOutputParam } from '../utils/paramUtils';

export const useOutputProcessor = (params: DisplayParams) => {
  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      console.log('[useOutputProcessor] Raw output param before processing:', params.output);
      const processedOutput = processOutputParam(params.output);
      
      if (processedOutput !== params.output) {
        console.log('[useOutputProcessor] Processed output param from:', params.output, 'to:', processedOutput);
        params.output = processedOutput;
      } else {
        console.log('[useOutputProcessor] Output param already in correct format:', params.output);
      }
    }
  }, [params.output]);
};
