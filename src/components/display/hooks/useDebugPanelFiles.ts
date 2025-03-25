
import { useState } from 'react';
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
  console.log('[useDebugPanelFiles] Initializing with params:', props.params);
  const [isCommitting, setIsCommitting] = useState(false);
  
  // Use the URL management hook
  const {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings: originalCommitSettings,
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

  // Enhanced commit settings function with proper redirect handling
  const commitSettings = () => {
    console.log('[useDebugPanelFiles] Commit settings initiated');
    
    try {
      // Prevent multiple clicks
      if (isCommitting) {
        console.log('[useDebugPanelFiles] Already committing, ignoring click');
        return;
      }
      
      setIsCommitting(true);
      
      // Call the original commit settings function
      const commitUrl = originalCommitSettings();
      
      // Log the URL
      console.log('[useDebugPanelFiles] Committing with URL:', commitUrl);
      
      // Directly force a full reload with the new URL
      // Using replace to avoid history issues
      if (commitUrl) {
        console.log('[useDebugPanelFiles] Redirecting to:', commitUrl);
        // Set localStorage flag to prevent auto-debug on the destination page
        localStorage.setItem('userExplicitlyExitedDebug', 'true');
        
        // Use window.location.replace for a clean redirect without browser history entry
        window.location.replace(commitUrl);
      } else {
        console.error('[useDebugPanelFiles] Failed to generate commit URL');
        setIsCommitting(false);
      }
    } catch (error) {
      console.error('[useDebugPanelFiles] Error during commit:', error);
      setIsCommitting(false);
    }
  };

  return {
    generateUrl,
    applySettings,
    resetDisplay,
    commitSettings, // Use the enhanced version
    copyUrl,
    selectFile,
    formatFileName,
    isCurrentFile,
    formatTime
  };
};
