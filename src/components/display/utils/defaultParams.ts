
import { DisplayParams } from '../types';

/**
 * Returns the default display parameters
 */
export const getDefaultParams = (): DisplayParams => {
  return {
    output: null,
    showMode: 'fill',
    position: 'center',
    refreshInterval: 5, // Default refresh interval is 5 seconds
    backgroundColor: '#000000',
    caption: null,
    captionPosition: 'bottom-center',
    captionSize: 'medium',
    captionColor: '#ffffff',
    captionFont: 'Helvetica, Arial, sans-serif', // Default font family is Helvetica
    captionBgColor: '#000000',
    captionBgOpacity: 0.5,
    transition: 'fade',
    debugMode: false
  };
};
