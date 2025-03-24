
import { DisplayParams } from '../types';

/**
 * Returns the default display parameters
 */
export const getDefaultParams = (): DisplayParams => {
  return {
    output: null,
    showMode: 'contain',
    position: 'center',
    refreshInterval: 0,
    backgroundColor: '#000000',
    caption: null,
    captionPosition: 'bottom',
    captionSize: 'medium',
    captionColor: '#ffffff',
    captionFont: 'sans',
    captionBgColor: '#000000',
    captionBgOpacity: 0.5,
    transition: 'fade',
    debugMode: false
  };
};
