
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
    return paramValue !== null ? paramValue : defaultValue;
  }, [searchParams]);
  
  // Function to update a parameter in the URL
  const updateParam = useCallback((key: string, value: string | null) => {
    if (value) {
      searchParams.set(key, value);
    } else {
      searchParams.delete(key);
    }
    setSearchParams(searchParams);
  }, [searchParams, setSearchParams]);
  
  // Construct the display parameters from the URL
  const displayParams: DisplayParams = {
    output: decodeComplexOutputParam(getParam('output', null)),
    showMode: getParam('showMode', 'contain') as DisplayParams['showMode'],
    position: getParam('position', 'center') as DisplayParams['position'],
    refreshInterval: parseInt(getParam('refreshInterval', '5') || '5', 10), // Default to 5 seconds
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
  
  return {
    displayParams,
    updateParam,
    location
  };
};
