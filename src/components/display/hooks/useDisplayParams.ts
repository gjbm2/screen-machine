
import { useCallback } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { DisplayParams } from '../types';
import { decodeComplexOutputParam, processOutputParam } from '../utils/paramUtils';
import { getDefaultParams } from '../utils/defaultParams';

export const useDisplayParams = () => {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  
  // Function to safely get a parameter from the URL
  const getParam = useCallback((key: string, defaultValue: string | null = null): string | null => {
    const paramValue = searchParams.get(key);
    console.log(`[useDisplayParams] Getting param ${key}, raw value:`, paramValue);
    return paramValue !== null ? paramValue : defaultValue;
  }, [searchParams]);
  
  // Function to update a parameter in the URL
  const updateParam = useCallback((key: string, value: string | null) => {
    console.log(`[useDisplayParams] Updating param ${key} to:`, value);
    if (value) {
      searchParams.set(key, value);
    } else {
      searchParams.delete(key);
    }
    setSearchParams(searchParams);
  }, [searchParams, setSearchParams]);
  
  // Get the raw output parameter for logging
  const rawOutput = searchParams.get('output');
  console.log('[useDisplayParams] Raw output param from URL:', rawOutput);
  
  // Decode the output parameter - improved handling
  let decodedOutput = null;
  if (rawOutput) {
    if (rawOutput.startsWith('http://') || rawOutput.startsWith('https://')) {
      // Already a valid URL, use as-is
      decodedOutput = rawOutput;
      console.log('[useDisplayParams] Output is already a valid URL:', decodedOutput);
    } else if (rawOutput.includes('%') && (rawOutput.includes('http%3A') || rawOutput.includes('https%3A'))) {
      // Needs decoding as it appears to be an encoded external URL
      decodedOutput = decodeComplexOutputParam(rawOutput);
      console.log('[useDisplayParams] Decoded output param:', decodedOutput);
    } else {
      // Local path - normalize it
      decodedOutput = processOutputParam(rawOutput);
      console.log('[useDisplayParams] Normalized output path:', decodedOutput);
    }
  }
  
  // Construct the display parameters from the URL
  const displayParams: DisplayParams = {
    output: decodedOutput,
    showMode: getParam('showMode', 'contain') as DisplayParams['showMode'],
    position: getParam('position', 'center') as DisplayParams['position'],
    refreshInterval: parseInt(getParam('refreshInterval', '0') || '0', 10),
    backgroundColor: getParam('backgroundColor', '#000000') || '#000000',
    caption: decodeComplexOutputParam(getParam('caption', null)),
    captionPosition: getParam('captionPosition', 'bottom-center') as DisplayParams['captionPosition'],
    captionSize: getParam('captionSize', 'medium') || 'medium',
    captionColor: getParam('captionColor', '#ffffff') || '#ffffff',
    captionFont: getParam('captionFont', 'sans') || 'sans',
    captionBgColor: getParam('captionBgColor', '#000000') || '#000000',
    captionBgOpacity: parseFloat(getParam('captionBgOpacity', '0.5') || '0.5'),
    transition: getParam('transition', 'fade') as DisplayParams['transition'],
    debugMode: getParam('debug', 'false') === 'true'
  };
  
  console.log('[useDisplayParams] Constructed display params:', displayParams);
  
  return {
    displayParams,
    updateParam,
    location
  };
};
