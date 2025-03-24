
import { useState } from 'react';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from '../types';
import { getDefaultParams } from '../utils';

interface UseDebugPanelStateProps {
  params: DisplayParams;
}

export const useDebugPanelState = ({ params }: UseDebugPanelStateProps) => {
  const defaultParams = getDefaultParams();
  
  // Tab and content state
  const [activeTab, setActiveTab] = useState("files");
  const [customUrl, setCustomUrl] = useState(params.output || "");
  
  // Display settings
  const [showMode, setShowMode] = useState<ShowMode>(params.showMode);
  const [position, setPosition] = useState<PositionMode>(params.position);
  const [refreshInterval, setRefreshInterval] = useState(params.refreshInterval);
  const [backgroundColor, setBackgroundColor] = useState(params.backgroundColor);
  const [transition, setTransition] = useState<TransitionType>(params.transition || "cut");
  
  // Caption settings
  const [caption, setCaption] = useState(params.caption || "");
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>(params.captionPosition || "bottom-center");
  const [captionSize, setCaptionSize] = useState(params.captionSize || "16px");
  const [captionColor, setCaptionColor] = useState(params.captionColor || "ffffff");
  const [captionFont, setCaptionFont] = useState(params.captionFont || "Arial, sans-serif");
  const [captionBgColor, setCaptionBgColor] = useState(params.captionBgColor || "#000000");
  const [captionBgOpacity, setCaptionBgOpacity] = useState(params.captionBgOpacity || 0.7);
  
  // Copy state
  const [copied, setCopied] = useState(false);
  
  // Metadata state
  const [metadataEntries, setMetadataEntries] = useState<Array<{key: string, value: string}>>([]);
  const [previewCaption, setPreviewCaption] = useState<string | null>(null);

  const resetSettings = () => {
    setShowMode(defaultParams.showMode);
    setPosition(defaultParams.position);
    setRefreshInterval(defaultParams.refreshInterval);
    setBackgroundColor(defaultParams.backgroundColor);
    setCaption('');
    setCaptionPosition(defaultParams.captionPosition);
    setCaptionSize(defaultParams.captionSize);
    setCaptionColor(defaultParams.captionColor);
    setCaptionFont(defaultParams.captionFont);
    setCaptionBgColor(defaultParams.captionBgColor);
    setCaptionBgOpacity(defaultParams.captionBgOpacity);
    setTransition(defaultParams.transition);
  };

  return {
    activeTab,
    setActiveTab,
    customUrl,
    setCustomUrl,
    showMode,
    setShowMode,
    position,
    setPosition,
    refreshInterval, 
    setRefreshInterval,
    backgroundColor,
    setBackgroundColor,
    caption,
    setCaption,
    captionPosition,
    setCaptionPosition,
    captionSize,
    setCaptionSize,
    captionColor,
    setCaptionColor,
    captionFont,
    setCaptionFont,
    captionBgColor,
    setCaptionBgColor,
    captionBgOpacity,
    setCaptionBgOpacity,
    transition,
    setTransition,
    copied,
    setCopied,
    metadataEntries,
    setMetadataEntries,
    previewCaption,
    setPreviewCaption,
    resetSettings
  };
};
