
import { DisplayParams } from '../types';
import { useDebugPanelUrls } from './useDebugPanelUrls';
import { useDebugPanelFileManagement } from './useDebugPanelFileManagement';

interface UseDebugPanelFilesProps {
  params: DisplayParams;
  customUrl: string;
  showMode: DisplayParams['showMode'];
  position: DisplayParams['position'];
  refreshInterval: number;
  backgroundColor: string;
  caption: string | null;
  captionPosition: DisplayParams['captionPosition'];
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  transition: DisplayParams['transition'];
  setCopied: (value: boolean) => void;
}

export const useDebugPanelFiles = (props: UseDebugPanelFilesProps) => {
  // Use the URL management hook
  const {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl
  } = useDebugPanelUrls(props);

  // Use the file management hook
  const {
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  } = useDebugPanelFileManagement({
    params: props.params
  });

  return {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings,
    copyUrl,
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  };
};
