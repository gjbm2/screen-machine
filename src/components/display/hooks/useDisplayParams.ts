
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DisplayParams } from '@/components/display/types';

export const useDisplayParams = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Extract and parse all URL parameters
  const params: DisplayParams = {
    output: searchParams.get('output') ? decodeURIComponent(searchParams.get('output') || '') : null,
    showMode: (searchParams.get('show') || 'fit') as DisplayParams['showMode'],
    position: (searchParams.get('position') || 'center') as DisplayParams['position'],
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
    data: searchParams.has('data') ? searchParams.get('data') : undefined,
    caption: searchParams.get('caption') ? decodeURIComponent(searchParams.get('caption') || '') : null,
    captionPosition: searchParams.get('caption-position') as DisplayParams['captionPosition'] || 'bottom-center',
    captionSize: searchParams.get('caption-size') || '16px',
    captionColor: searchParams.get('caption-color') || 'ffffff',
    captionFont: searchParams.get('caption-font') ? decodeURIComponent(searchParams.get('caption-font') || '') : 'Arial, sans-serif',
    captionBgColor: searchParams.get('caption-bg-color') || '#000000',
    captionBgOpacity: searchParams.get('caption-bg-opacity') ? parseFloat(searchParams.get('caption-bg-opacity') || '0.7') : 0.7,
    transition: searchParams.get('transition') as DisplayParams['transition'] || 'cut',
  };

  // Initialize debug mode if no output is provided
  const redirectToDebugMode = () => {
    if (!params.output && !params.debugMode) {
      const queryParams = new URLSearchParams();
      queryParams.set('debug', 'true');
      if (params.showMode) queryParams.set('show', params.showMode);
      if (params.position) queryParams.set('position', params.position);
      if (params.refreshInterval) queryParams.set('refresh', params.refreshInterval.toString());
      if (params.backgroundColor) queryParams.set('background', params.backgroundColor);
      navigate(`/display?${queryParams.toString()}`);
    }
  };

  return {
    params,
    redirectToDebugMode
  };
};
